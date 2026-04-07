import os
import subprocess

from .wiki_lint import format_lint_report, run_wiki_lint
from .wiki_ops import RAW_DIR, WIKI_DIR, parse_frontmatter, resolve_path_under, resolve_wiki_page_path

QMD_PATH = os.environ.get("QMD_PATH", "qmd")

TOOL_DEFINITIONS = [
    {
        "name": "list_wiki",
        "description": "List all wiki pages with their title and summary from frontmatter.",
        "parameters": {},
    },
    {
        "name": "read_wiki",
        "description": "Read the full content of a wiki page.",
        "parameters": {
            "page": {
                "type": "string",
                "description": "Page name without .md extension, e.g. 'cognitive-biases'",
            }
        },
    },
    {
        "name": "write_wiki",
        "description": "Create or update a wiki page. Content should be full markdown with YAML frontmatter matching our schema.",
        "parameters": {
            "page": {
                "type": "string",
                "description": "Page name without .md extension",
            },
            "content": {
                "type": "string",
                "description": "Full markdown content including YAML frontmatter",
            },
        },
    },
    {
        "name": "read_source",
        "description": "Read a raw source document.",
        "parameters": {
            "path": {
                "type": "string",
                "description": "Filename within raw/, e.g. 'article.md'",
            }
        },
    },
    {
        "name": "list_sources",
        "description": "List all files in the raw/ source directory.",
        "parameters": {},
    },
    {
        "name": "search_wiki",
        "description": "Search wiki pages using qmd (hybrid BM25/vector search).",
        "parameters": {
            "query": {
                "type": "string",
                "description": "Search query",
            }
        },
    },
    {
        "name": "lint_wiki",
        "description": "Validate wiki integrity: frontmatter, links, sources, index, and log structure.",
        "parameters": {},
    },
]


def list_wiki() -> str:
    WIKI_DIR.mkdir(exist_ok=True)
    pages = []
    for f in sorted(WIKI_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        title = fm.get("title", f.stem)
        summary = fm.get("summary", "")
        pages.append(f"- [[{f.stem}]] — {title}: {summary}")
    return "\n".join(pages) if pages else "(no pages yet)"


def read_wiki(page: str) -> str:
    path = resolve_wiki_page_path(page)
    if not path.exists():
        return f"Page '{page}' does not exist."
    return path.read_text(encoding="utf-8")


def write_wiki(page: str, content: str) -> str:
    path = resolve_wiki_page_path(page)
    created = not path.exists()
    path.write_text(content, encoding="utf-8")
    msg = f"{'Created' if created else 'Updated'} wiki/{page}.md"
    if page not in ("log", "index"):
        msg += "\nReminder: update index.md and append to log.md when done with this operation."
    return msg


def read_source(path: str) -> str:
    full = resolve_path_under(RAW_DIR, path)
    if not full.exists():
        return f"Source '{path}' does not exist."
    # Handle binary files (PDFs, etc.)
    suffix = full.suffix.lower()
    if suffix == ".pdf":
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(full), "-"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
            return f"Could not extract text from PDF '{path}'. The file may be scanned/image-based."
        except FileNotFoundError:
            return f"Cannot read PDF '{path}' — pdftotext is not installed."
        except subprocess.TimeoutExpired:
            return f"PDF text extraction timed out for '{path}'."
    try:
        return full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return full.read_text(encoding="latin-1")
        except Exception:
            return f"Cannot read '{path}' — unsupported file encoding."


def list_sources() -> str:
    RAW_DIR.mkdir(exist_ok=True)
    files = []
    for f in sorted(RAW_DIR.rglob("*")):
        if f.is_file():
            rel = f.relative_to(RAW_DIR)
            files.append(str(rel))
    return "\n".join(files) if files else "(no source files)"


_qmd_collection_initialized = False


def _ensure_qmd_collection():
    global _qmd_collection_initialized
    if _qmd_collection_initialized:
        return
    subprocess.run(
        [QMD_PATH, "collection", "add", str(WIKI_DIR.resolve()), "--name", "wiki"],
        capture_output=True, text=True, timeout=15,
    )
    _qmd_collection_initialized = True


def search_wiki(query: str) -> str:
    try:
        _ensure_qmd_collection()
        result = subprocess.run(
            [QMD_PATH, "search", query, "-c", "wiki"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return _fallback_search(query)
        return result.stdout.strip() if result.stdout.strip() else "No results found."
    except FileNotFoundError:
        return _fallback_search(query)
    except subprocess.TimeoutExpired:
        return "Search timed out."


def _fallback_search(query: str) -> str:
    """Simple substring search when qmd is not available."""
    WIKI_DIR.mkdir(exist_ok=True)
    terms = query.lower().split()
    results = []
    for f in sorted(WIKI_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        if all(t in content.lower() for t in terms):
            fm = parse_frontmatter(content)
            title = fm.get("title", f.stem)
            results.append(f"- [[{f.stem}]] — {title}")
    return "\n".join(results) if results else "No results found."


def lint_wiki() -> str:
    return format_lint_report(run_wiki_lint())


TOOL_EXECUTORS = {
    "list_wiki": lambda args: list_wiki(),
    "read_wiki": lambda args: read_wiki(args["page"]),
    "write_wiki": lambda args: write_wiki(args["page"], args["content"]),
    "read_source": lambda args: read_source(args["path"]),
    "list_sources": lambda args: list_sources(),
    "search_wiki": lambda args: search_wiki(args["query"]),
    "lint_wiki": lambda args: lint_wiki(),
}


def execute_tool(name: str, args: dict) -> str:
    executor = TOOL_EXECUTORS.get(name)
    if not executor:
        return f"Unknown tool: {name}"
    try:
        return executor(args)
    except Exception as e:
        return f"Error executing {name}: {e}"
