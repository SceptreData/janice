import re
from pathlib import Path

import yaml

WIKI_DIR = Path("wiki")
RAW_DIR = Path("raw")
SCHEMA_PATH = Path("schema.md")
SOUL_PATH = Path("SOUL.md")

FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n*", re.DOTALL)
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
PAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def extract_frontmatter_block(text: str) -> str | None:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return None
    return match.group(1)


def parse_frontmatter(text: str) -> dict:
    block = extract_frontmatter_block(text)
    if block is None:
        return {}
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_PATTERN.sub("", text, count=1)


def normalize_wikilink_target(value: str) -> str:
    target = value.split("|", 1)[0].strip()
    return re.sub(r"\s+", "-", target).lower()


def iter_wikilinks(text: str) -> list[dict]:
    links = []
    for match in WIKILINK_PATTERN.finditer(text):
        raw = match.group(1).strip()
        target_raw, _, label_raw = raw.partition("|")
        target = normalize_wikilink_target(target_raw)
        label = label_raw.strip() if label_raw else target_raw.strip()
        line = text.count("\n", 0, match.start()) + 1
        links.append(
            {
                "raw": raw,
                "target": target,
                "label": label,
                "line": line,
            }
        )
    return links


def resolve_path_under(base_dir: Path, relative_path: str) -> Path:
    base_dir.mkdir(exist_ok=True)
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("Path must be relative.")

    candidate = (base_dir / relative).resolve(strict=False)
    base_resolved = base_dir.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError("Path escapes the allowed directory.") from exc
    return candidate


def resolve_wiki_page_path(page: str) -> Path:
    if not PAGE_NAME_PATTERN.fullmatch(page):
        raise ValueError(
            "Invalid wiki page name. Use lowercase letters, numbers, hyphens, or underscores."
        )
    return resolve_path_under(WIKI_DIR, f"{page}.md")
