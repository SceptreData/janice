import re
from collections import defaultdict
from pathlib import Path

import yaml

from .schema import LintIssue, LintReport
from .wiki_ops import RAW_DIR, WIKI_DIR, extract_frontmatter_block, iter_wikilinks

REQUIRED_FRONTMATTER_FIELDS = ("title", "summary", "tags", "sources", "created", "updated")
META_PAGES = {"index", "log"}

_TYPE_KEYWORDS = {
    "Sources": {"source", "summary", "document", "report", "transcript", "meeting"},
    "Entities": {"entity", "person", "organization", "place", "company", "team", "vendor"},
    "Concepts": {"concept", "idea", "theory", "framework", "principle"},
    "Topics": {"project", "program", "initiative", "legal", "policy", "process", "procurement", "negotiation"},
    "Meta": {"meta", "index", "log", "alias"},
}
_INDEX_SECTION_PATTERN = re.compile(r"^##\s+(?P<section>.+?)\s*$")
_INDEX_ENTRY_PATTERN = re.compile(r"^- \[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_LOG_ENTRY_PATTERN = re.compile(
    r"^## \[\d{4}-\d{2}-\d{2}\] (ingest|query|lint|update) \| .+?$",
    re.MULTILINE,
)


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    page: str | None = None,
    line: int | None = None,
) -> LintIssue:
    return LintIssue(
        severity=severity,
        code=code,
        message=message,
        page=page,
        line=line,
    )


def _display_path(page: str | None) -> str:
    return f"[[{page}]]" if page else "wiki"


def _normalize_title(value: str) -> str:
    normalized = re.sub(r"\s*\([^)]*\)", "", value).strip().lower()
    return re.sub(r"\s+", " ", normalized)


def _page_category(name: str, frontmatter: dict) -> str:
    if name in META_PAGES:
        return "Meta"

    tags = {str(tag).lower() for tag in frontmatter.get("tags", []) if isinstance(tag, str)}
    for section, keywords in _TYPE_KEYWORDS.items():
        if tags & keywords:
            return section

    if frontmatter.get("sources"):
        return "Sources"
    return "Topics"


def _validate_index(
    index_path: Path,
    page_names: set[str],
    frontmatters: dict[str, dict],
    issues: list[LintIssue],
) -> None:
    text = index_path.read_text(encoding="utf-8")
    section_entries: dict[str, list[str]] = defaultdict(list)
    listed_pages: set[str] = set()
    current_section: str | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        section_match = _INDEX_SECTION_PATTERN.match(line)
        if section_match:
            current_section = section_match.group("section")
            continue

        entry_match = _INDEX_ENTRY_PATTERN.match(line)
        if not entry_match or current_section is None:
            continue

        page = entry_match.group(1)
        section_entries[current_section].append(page)
        listed_pages.add(page)
        if page not in page_names:
            issues.append(
                _issue(
                    "error",
                    "index_missing_page",
                    f"Index references missing page [[{page}]].",
                    page="index",
                    line=lineno,
                )
            )

    for section, entries in section_entries.items():
        sorted_entries = sorted(entries, key=str.casefold)
        if entries != sorted_entries:
            issues.append(
                _issue(
                    "warning",
                    "index_not_sorted",
                    f"Section '{section}' is not alphabetized.",
                    page="index",
                )
            )

    for name in sorted(page_names):
        if name not in listed_pages:
            issues.append(
                _issue(
                    "warning",
                    "index_missing_entry",
                    f"Page [[{name}]] is not listed in index.md.",
                    page="index",
                )
            )
            continue

        expected_section = _page_category(name, frontmatters.get(name, {}))
        actual_section = next(
            (section for section, entries in section_entries.items() if name in entries),
            None,
        )
        if actual_section and actual_section != expected_section:
            issues.append(
                _issue(
                    "warning",
                    "index_wrong_section",
                    f"Page [[{name}]] is listed under '{actual_section}' but looks like '{expected_section}'.",
                    page="index",
                )
            )


def _validate_log(log_path: Path, issues: list[LintIssue]) -> None:
    text = log_path.read_text(encoding="utf-8")
    entries = list(_LOG_ENTRY_PATTERN.finditer(text))
    if not entries:
        issues.append(
            _issue(
                "error",
                "log_missing_entries",
                "log.md has no entries matching the required log format.",
                page="log",
            )
        )
        return

    for match in entries:
        next_entry = text.find("\n## ", match.end())
        entry_text = text[match.start() : next_entry if next_entry != -1 else len(text)]
        if "- **Created:**" not in entry_text or "- **Updated:**" not in entry_text:
            issues.append(
                _issue(
                    "warning",
                    "log_missing_fields",
                    "A log entry is missing a Created or Updated bullet.",
                    page="log",
                    line=text.count("\n", 0, match.start()) + 1,
                )
            )


def run_wiki_lint(
    wiki_dir: Path = WIKI_DIR,
    raw_dir: Path = RAW_DIR,
) -> LintReport:
    wiki_dir.mkdir(exist_ok=True)
    raw_dir.mkdir(exist_ok=True)

    issues: list[LintIssue] = []
    page_paths = sorted(p for p in wiki_dir.glob("*.md") if p.name != ".gitkeep")
    page_names = {path.stem for path in page_paths}
    frontmatters: dict[str, dict] = {}
    inbound_links: dict[str, set[str]] = {name: set() for name in page_names}
    referenced_sources: set[str] = set()
    title_index: dict[str, list[str]] = defaultdict(list)

    for path in page_paths:
        page = path.stem
        text = path.read_text(encoding="utf-8")

        block = extract_frontmatter_block(text)
        if block is None:
            issues.append(
                _issue(
                    "error",
                    "missing_frontmatter",
                    f"{_display_path(page)} is missing YAML frontmatter.",
                    page=page,
                    line=1,
                )
            )
            frontmatters[page] = {}
        else:
            try:
                data = yaml.safe_load(block) or {}
            except yaml.YAMLError as exc:
                problem_mark = getattr(exc, "problem_mark", None)
                issues.append(
                    _issue(
                        "error",
                        "invalid_frontmatter",
                        f"{_display_path(page)} has invalid YAML frontmatter: {exc}.",
                        page=page,
                        line=(problem_mark.line + 2) if problem_mark else 1,
                    )
                )
                data = {}

            if data and not isinstance(data, dict):
                issues.append(
                    _issue(
                        "error",
                        "invalid_frontmatter_shape",
                        f"{_display_path(page)} frontmatter must be a mapping.",
                        page=page,
                        line=1,
                    )
                )
                data = {}

            frontmatters[page] = data
            missing_fields = [field for field in REQUIRED_FRONTMATTER_FIELDS if field not in data]
            if missing_fields:
                issues.append(
                    _issue(
                        "error",
                        "missing_frontmatter_fields",
                        f"{_display_path(page)} is missing required frontmatter fields: {', '.join(missing_fields)}.",
                        page=page,
                        line=1,
                    )
                )

            for field in ("tags", "sources"):
                value = data.get(field)
                if value is not None and not isinstance(value, list):
                    issues.append(
                        _issue(
                            "error",
                            "invalid_frontmatter_type",
                            f"{_display_path(page)} field '{field}' must be a list.",
                            page=page,
                            line=1,
                        )
                    )

            for source in data.get("sources", []):
                if not isinstance(source, str):
                    issues.append(
                        _issue(
                            "error",
                            "invalid_source_entry",
                            f"{_display_path(page)} has a non-string entry in sources.",
                            page=page,
                            line=1,
                        )
                    )
                    continue
                referenced_sources.add(source)
                if not (raw_dir / source).exists():
                    issues.append(
                        _issue(
                            "error",
                            "missing_source_file",
                            f"{_display_path(page)} references missing raw source `{source}`.",
                            page=page,
                            line=1,
                        )
                    )

            title = data.get("title")
            if isinstance(title, str) and title.strip():
                title_index[_normalize_title(title)].append(page)

        for link in iter_wikilinks(text):
            target = link["target"]
            if target in page_names:
                inbound_links[target].add(page)
            else:
                issues.append(
                    _issue(
                        "error",
                        "broken_wikilink",
                        f"{_display_path(page)} links to missing page [[{link['raw']}]].",
                        page=page,
                        line=link["line"],
                    )
                )

    for normalized_title, pages in sorted(title_index.items()):
        if len(pages) > 1:
            issues.append(
                _issue(
                    "warning",
                    "duplicate_title",
                    f"Multiple pages appear to describe the same subject: {', '.join(f'[[{page}]]' for page in sorted(pages))}.",
                )
            )

    for page, sources in sorted(inbound_links.items()):
        if page == "index":
            continue
        if not sources:
            issues.append(
                _issue(
                    "warning",
                    "orphan_page",
                    f"{_display_path(page)} has no inbound wikilinks.",
                    page=page,
                )
            )

    index_path = wiki_dir / "index.md"
    if index_path.exists():
        _validate_index(index_path, page_names, frontmatters, issues)
    else:
        issues.append(_issue("error", "missing_index", "wiki/index.md is missing.", page="index"))

    log_path = wiki_dir / "log.md"
    if log_path.exists():
        _validate_log(log_path, issues)
    else:
        issues.append(_issue("error", "missing_log", "wiki/log.md is missing.", page="log"))

    raw_files = sorted(
        str(path.relative_to(raw_dir))
        for path in raw_dir.rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    )
    unused_sources = [path for path in raw_files if path not in referenced_sources]
    if unused_sources:
        preview = ", ".join(f"`{path}`" for path in unused_sources[:5])
        if len(unused_sources) > 5:
            preview += f", and {len(unused_sources) - 5} more"
        issues.append(
            _issue(
                "warning",
                "unreferenced_sources",
                f"{len(unused_sources)} raw sources are not referenced by any wiki page: {preview}.",
            )
        )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    summary = (
        f"Wiki lint found {error_count} error(s) and {warning_count} warning(s) "
        f"across {len(page_paths)} page(s)."
    )

    return LintReport(
        ok=error_count == 0,
        summary=summary,
        issues=sorted(
            issues,
            key=lambda issue: (issue.severity != "error", issue.page or "", issue.line or 0, issue.code),
        ),
    )


def format_lint_report(report: LintReport) -> str:
    lines = ["## Wiki Lint", "", report.summary]

    if report.ok and not report.issues:
        lines.extend(["", "No issues found."])
        return "\n".join(lines)

    for severity in ("error", "warning"):
        severity_issues = [issue for issue in report.issues if issue.severity == severity]
        if not severity_issues:
            continue
        lines.extend(["", "### Errors" if severity == "error" else "### Warnings"])
        for issue in severity_issues:
            location = []
            if issue.page:
                location.append(f"[[{issue.page}]]")
            if issue.line:
                location.append(f"line {issue.line}")
            prefix = f" ({', '.join(location)})" if location else ""
            lines.append(f"- `{issue.code}`{prefix}: {issue.message}")

    return "\n".join(lines)
