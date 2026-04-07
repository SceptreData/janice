# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Janice is an LLM-powered wiki builder. Users upload source documents, chat with an LLM that reads those sources, and the LLM creates/maintains interlinked markdown wiki pages. The frontend is a single-page app with a chat pane (left) and a wiki browser + graph view (right).

## Architecture

- **FastAPI backend** (`app/main.py`) — serves the API and static frontend.
- **LLM layer** (`app/llm.py`) — manages the chat loop with prompt-based tool calling via OpenRouter (using the OpenAI SDK). The LLM emits `\`\`\`tool` fenced blocks which are parsed and executed in a loop with a hard cap of 15 rounds per message (configurable with `LLM_MAX_TOOL_ROUNDS`).
- **Tool system** (`app/tools.py`) — seven tools (list_wiki, read_wiki, write_wiki, read_source, list_sources, search_wiki, lint_wiki) that the LLM calls to read/write files and validate the wiki. Search uses `qmd` (hybrid BM25/vector) with a substring fallback.
- **Schema** (`schema.md`) — the system prompt injected into every LLM conversation. Defines wiki conventions, operations (ingest, query, lint), and the three-layer model (raw/ -> wiki/ -> schema.md).
- **Frontend** (`app/static/`) — vanilla HTML/CSS/JS. Uses `marked.js` for markdown rendering, `d3.js` for the wiki graph, SSE for streaming chat responses.

## Key Data Directories

- `raw/` — user-uploaded source documents (gitignored, read-only to the LLM)
- `wiki/` — LLM-maintained markdown pages with YAML frontmatter and `[[wikilinks]]`

## Commands

```bash
# Run locally
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Run with Docker
docker compose up --build

# Docker watch mode (auto-sync app/ changes)
docker compose watch
```

## Environment

Requires `OPENROUTER_API_KEY`. Optional `LLM_MODEL` (defaults to `google/gemma-3-27b-it:free`). Optional `QMD_PATH` for the qmd search binary. Copy `.env.example` to `.env`.

## Conventions

- Wiki pages use YAML frontmatter (title, summary, tags, sources, created, updated) and `[[wikilinks]]` for cross-references.
- `wiki/index.md` is a categorized catalog (Sources, Entities, Concepts, Topics, Meta). `wiki/log.md` is append-only chronological operations log.
- Tool definitions in `tools.py` use a custom JSON schema (not OpenAI function calling) — the LLM parses them from the system prompt.
- SSE event types: `text`, `tool_call`, `tool_result`, `wiki_update`, `done`.
- No test suite currently exists.
