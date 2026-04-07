import logging
import json
import os
import re
import shutil
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .llm import chat_stream
from .schema import ChatRequest, GraphData, GraphEdge, GraphNode, WikiPage
from .tools import parse_frontmatter as _parse_frontmatter, WIKI_DIR, RAW_DIR

# Structured logging setup — one JSON line per event to stdout
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_janice_logger = logging.getLogger("janice")
_janice_logger.addHandler(_handler)
_janice_logger.setLevel(logging.INFO)
_janice_logger.propagate = False

logger = _janice_logger

app = FastAPI(title="Janice — LLM Wiki")


@app.on_event("startup")
async def warn_missing_key():
    if not os.environ.get("OPENROUTER_API_KEY"):
        logger.warning(
            "\n"
            "  *** OPENROUTER_API_KEY is not set ***\n"
            "  Chat will not work. Copy .env.example to .env and add your key.\n"
        )

STATIC_DIR = Path("app/static")


# --- SSE Chat ---

@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not os.environ.get("OPENROUTER_API_KEY"):
        return JSONResponse(
            status_code=503,
            content={"error": "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."},
        )

    history = [{"role": m.role, "content": m.content} for m in req.history]

    async def event_stream():
        async for event in chat_stream(req.message, history, model=req.model):
            evt = event["event"]
            data = event["data"]
            yield f"event: {evt}\ndata: {data}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- Models API ---

# Well-known model families worth showing (prefix match on model ID)
_POPULAR_PREFIXES = (
    "google/gemma-",
    "google/gemini-",
    "meta-llama/",
    "mistralai/",
    "qwen/",
    "deepseek/",
    "nvidia/",
    "microsoft/",
)

_PINNED_MODELS = (
    "google/gemini-3-flash-preview",
    "z-ai/glm-5"
)

_models_cache: dict = {"models": None}


@app.get("/api/models")
async def list_models():
    if _models_cache["models"] is not None:
        return _models_cache["models"]

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    models = []
    seen = set()
    for m in data.get("data", []):
        mid = m["id"]
        pricing = m.get("pricing", {})
        is_free = pricing.get("prompt") == "0" and pricing.get("completion") == "0"
        is_pinned = mid in _PINNED_MODELS

        if not is_pinned and not is_free:
            continue
        if not is_pinned and not any(mid.startswith(p) for p in _POPULAR_PREFIXES):
            continue
        if mid in seen:
            continue
        seen.add(mid)

        models.append({
            "id": mid,
            "name": m.get("name", mid),
            "context_length": m.get("context_length"),
        })

    models.sort(key=lambda m: m["name"])
    _models_cache["models"] = models
    return models


# --- Wiki API ---


@app.get("/api/wiki")
async def list_wiki():
    WIKI_DIR.mkdir(exist_ok=True)
    pages = []
    for f in sorted(WIKI_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        pages.append(WikiPage(
            name=f.stem,
            title=fm.get("title", f.stem),
            summary=fm.get("summary", ""),
            tags=fm.get("tags", []),
        ))
    return pages


_TYPE_KEYWORDS = {
    "source": {"source", "summary", "document", "report", "transcript", "meeting"},
    "entity": {"entity", "person", "organization", "place", "company", "team"},
    "concept": {"concept", "idea", "theory", "framework", "principle"},
    "meta": {"meta", "index", "log"},
}


def _infer_node_type(name: str, fm: dict) -> str:
    """Derive a graph node type from page name and frontmatter tags."""
    if name in ("index", "log"):
        return "meta"
    tags = {t.lower() for t in fm.get("tags", [])}
    for node_type, keywords in _TYPE_KEYWORDS.items():
        if tags & keywords:
            return node_type
    # Fall back to checking if it has sources (likely a source summary page)
    if fm.get("sources"):
        return "source"
    return "topic"


@app.get("/api/wiki/graph")
async def wiki_graph():
    WIKI_DIR.mkdir(exist_ok=True)
    nodes = []
    edges = []
    page_names = set()

    for f in sorted(WIKI_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        name = f.stem
        page_names.add(name)
        node_type = _infer_node_type(name, fm)
        nodes.append(GraphNode(id=name, title=fm.get("title", name), type=node_type))

        # Extract [[wikilinks]]
        links = re.findall(r"\[\[([^\]]+)\]\]", content)
        for link in links:
            target = link.lower().replace(" ", "-")
            edges.append(GraphEdge(source=name, target=target))

    # Only include edges where both endpoints exist
    edges = [e for e in edges if e.source in page_names and e.target in page_names]

    return GraphData(nodes=nodes, edges=edges)


@app.get("/api/wiki/{page}")
async def get_wiki_page(page: str):
    path = WIKI_DIR / f"{page}.md"
    if not path.exists():
        return {"error": "not found"}
    content = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)
    # Strip frontmatter for the body
    body = re.sub(r"^---\n.*?\n---\n*", "", content, count=1, flags=re.DOTALL)
    return {"name": page, "frontmatter": fm, "body": body}


# --- Sources API ---

@app.get("/api/sources")
async def list_sources():
    RAW_DIR.mkdir(exist_ok=True)
    files = []
    for f in sorted(RAW_DIR.rglob("*")):
        if f.is_file():
            files.append(str(f.relative_to(RAW_DIR)))
    return files


def _normalize_source_name(name: str) -> str:
    """Normalize a source filename for fuzzy matching."""
    # Strip extension, lowercase, collapse separators to hyphens
    stem = re.sub(r"\.[^.]+$", "", name)
    return re.sub(r"[\s_]+", "-", stem).lower().strip("-")


@app.get("/api/sources/pending")
async def pending_sources():
    """Return source files not yet referenced in any wiki page's frontmatter."""
    RAW_DIR.mkdir(exist_ok=True)
    WIKI_DIR.mkdir(exist_ok=True)

    all_sources = {}
    for f in RAW_DIR.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(RAW_DIR))
            all_sources[_normalize_source_name(rel)] = rel

    ingested_keys = set()
    for f in WIKI_DIR.glob("*.md"):
        content = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        # Match against frontmatter sources field
        for src in fm.get("sources", []):
            ingested_keys.add(_normalize_source_name(src))
        # Also match against the wiki page name itself (LLM often slugifies the source name)
        ingested_keys.add(_normalize_source_name(f.stem))

    pending = [name for key, name in sorted(all_sources.items()) if key not in ingested_keys]
    return pending


@app.post("/api/sources")
async def upload_source(file: UploadFile):
    RAW_DIR.mkdir(exist_ok=True)
    dest = RAW_DIR / file.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"filename": file.filename}


# --- Static files (frontend) ---

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
