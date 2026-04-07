# Janice

An LLM-powered personal wiki. You feed it documents, chat with it, and it builds a structured, interlinked knowledge base of markdown files -- creating pages, extracting entities, cross-referencing everything, and maintaining an index and changelog. A single source document can touch 10-15 wiki pages.

Inspired by Karpathy's [LLM OS](https://x.com/karpathy/status/1723140519554105733) idea -- give an LLM a filesystem and let it organize knowledge for you.

## How It Works

You upload source documents (PDFs, markdown, meeting notes) into `raw/`. The LLM reads them and maintains a wiki of interlinked markdown pages in `wiki/`. A schema file (`schema.md`) defines the conventions the LLM follows. The LLM has a personality defined in `SOUL.md` -- by default she's a plucky southern librarian named Janice.

The chat interface uses prompt-based tool calling: the LLM emits tool calls in fenced code blocks, and the backend executes them in a loop. Tools let the LLM list, read, write, search, and lint wiki pages, and read source documents (including PDF text extraction via `pdftotext`).

The frontend is a two-pane layout -- chat on the left, wiki browser on the right with a page list, page viewer, and a D3 force-directed graph of wikilinks.

## Setup

Requires an [OpenRouter](https://openrouter.ai/) API key. Defaults to a free model (`google/gemma-3-27b-it:free`).

```bash
cp .env.example .env
# Add your OPENROUTER_API_KEY to .env
```

### Docker (recommended)

```bash
docker compose up --build
```

For development with auto-sync:

```bash
docker compose watch
```

### Local

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`.

## Usage

- **Chat** -- ask questions, request ingestion of sources, or ask for a wiki lint/health check.
- **Drop files** -- drag files onto the chat pane to upload them as sources, then click Ingest.
- **Browse** -- the right pane shows wiki pages, rendered markdown, and a graph of connections between pages.
- **Lint** -- the header button runs the backend wiki validator directly and reports broken links, schema drift, and missing metadata.
- **Model picker** -- switch between free OpenRouter models from the dropdown.
- **Themes** -- Everforest (default) and Gruvbox dark.

## Environment Variables

| Variable              | Required | Default                      | Description                                                 |
| --------------------- | -------- | ---------------------------- | ----------------------------------------------------------- |
| `OPENROUTER_API_KEY`  | Yes      | --                           | OpenRouter API key                                          |
| `LLM_MODEL`           | No       | `google/gemma-3-27b-it:free` | Model ID from OpenRouter                                    |
| `LLM_RATE_LIMIT`      | No       | `0`                          | Max requests per rate window; `0` disables local throttling |
| `LLM_RATE_WINDOW`     | No       | `60`                         | Rate window in seconds                                      |
| `LLM_MAX_TOOL_ROUNDS` | No       | `50`                         | Hard cap on tool-call rounds per chat                       |
| `QMD_PATH`            | No       | `qmd`                        | Path to qmd binary for hybrid search                        |

## What's Next

- Stronger ingest prompts -- current prompts under-extract entities and miss cross-references
- Agent skills -- let the LLM use richer tool chains (multi-step research, source comparison)
- Better log -- structured operation log with filtering, not just append-only markdown
- Page grouping -- smarter categorization in the index beyond the current flat type buckets
- Tests -- no test suite exists yet
- Wiki page deletion -- tools can create and update but not remove stale pages
- Error recovery -- failed ingest batches stop cold; needs retry or partial resume
