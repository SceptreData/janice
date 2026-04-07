import asyncio
import time
from dataclasses import dataclass, field

from .llm import chat_stream
from .log import log_event


@dataclass
class IngestState:
    status: str = "idle"  # idle | running | cancelling
    pending: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    current_batch: list[str] = field(default_factory=list)
    error: str | None = None
    task: asyncio.Task | None = None


_state = IngestState()

BATCH_SIZE = 2


def get_status() -> dict:
    return {
        "status": _state.status,
        "pending": list(_state.pending),
        "completed": list(_state.completed),
        "current_batch": list(_state.current_batch),
        "error": _state.error,
    }


def start_ingest(files: list[str], model: str | None = None) -> dict:
    if _state.status != "idle":
        return {"error": "Ingest already running", "status": _state.status}

    _state.status = "running"
    _state.pending = list(files)
    _state.completed = []
    _state.current_batch = []
    _state.error = None
    _state.task = asyncio.create_task(_run_ingest(files, model))
    return get_status()


def cancel_ingest() -> dict:
    if _state.status != "running":
        return {"error": "No ingest running", "status": _state.status}
    _state.status = "cancelling"
    return get_status()


async def _run_ingest(files: list[str], model: str | None) -> None:
    batches = [files[i:i + BATCH_SIZE] for i in range(0, len(files), BATCH_SIZE)]

    try:
        for batch in batches:
            if _state.status == "cancelling":
                log_event("ingest_cancelled")
                break

            _state.current_batch = list(batch)
            names = ", ".join(batch)
            message = f"Ingest these new source files: {names}"

            log_event("ingest_batch_start", files=batch)
            start = time.monotonic()

            async for _event in chat_stream(message, history=[], model=model):
                pass  # drain -- tool side effects write to disk

            elapsed = time.monotonic() - start
            log_event("ingest_batch_done", files=batch, elapsed_s=round(elapsed, 1))

            for f in batch:
                if f in _state.pending:
                    _state.pending.remove(f)
                _state.completed.append(f)

    except Exception as exc:
        log_event("ingest_error", error=str(exc))
        _state.error = str(exc)
    finally:
        _state.current_batch = []
        _state.status = "idle"
        _state.task = None
