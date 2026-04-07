import asyncio
import collections
import json
import logging
import os
import re
import time
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI, RateLimitError

from .tools import TOOL_DEFINITIONS, execute_tool
from .wiki_ops import SCHEMA_PATH, SOUL_PATH

logger = logging.getLogger("janice")


def _log_event(event: str, **kwargs) -> None:
    """Emit a structured log line as JSON."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **kwargs}
    logger.info(json.dumps(record))

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL = os.environ.get("LLM_MODEL", "google/gemma-3-27b-it:free")

client = AsyncOpenAI(
    base_url=OPENROUTER_BASE,
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
)

TOOL_SCHEMA_BLOCK = json.dumps(TOOL_DEFINITIONS, indent=2)

# Optional token-bucket rate limiter for outgoing API calls.
# Default: disabled. Set LLM_RATE_LIMIT to a positive integer to enable it.
_RATE_LIMIT = int(os.environ.get("LLM_RATE_LIMIT", "0"))
_RATE_WINDOW = float(os.environ.get("LLM_RATE_WINDOW", "60"))
_MAX_TOOL_ROUNDS = int(os.environ.get("LLM_MAX_TOOL_ROUNDS", "50"))
_request_timestamps: collections.deque[float] = collections.deque()
_rate_lock = asyncio.Lock()


async def _wait_for_capacity() -> float:
    """Block until a request slot is available. Returns seconds waited."""
    if _RATE_LIMIT <= 0:
        return 0.0

    waited = 0.0
    async with _rate_lock:
        now = time.monotonic()
        # Prune timestamps outside the window
        while _request_timestamps and _request_timestamps[0] <= now - _RATE_WINDOW:
            _request_timestamps.popleft()

        if len(_request_timestamps) >= _RATE_LIMIT:
            wait = _request_timestamps[0] - (now - _RATE_WINDOW)
            if wait > 0:
                waited = wait
                logger.info("Self-throttling for %.1fs to stay under %d req/%ds", wait, _RATE_LIMIT, int(_RATE_WINDOW))
                await asyncio.sleep(wait)
            # Prune again after sleeping
            now = time.monotonic()
            while _request_timestamps and _request_timestamps[0] <= now - _RATE_WINDOW:
                _request_timestamps.popleft()

        _request_timestamps.append(time.monotonic())
    return waited

SYSTEM_PROMPT_TEMPLATE = """You are Janice, a wiki maintainer AI. You incrementally build and maintain
a personal knowledge base of interlinked markdown files.

## Personality

{soul}

## Your Instructions

{schema}

## Available Tools

You have the following tools available. To call a tool, output a JSON block
wrapped in ```tool fences like this:

```tool
{{"tool": "tool_name", "args": {{"param": "value"}}}}
```

Available tools:
{tools}

IMPORTANT RULES:
- Call ONE tool at a time. Wait for the result before calling another.
- After receiving a tool result, you may call another tool or respond to the user.
- When you are done and want to respond to the user, just write your response
  as normal text (no tool block).
- When writing wiki pages, always include YAML frontmatter (title, summary, tags,
  sources, created, updated).
- When you create or update wiki pages, always update index.md and log.md too.
- Use [[wikilinks]] in wiki pages to link between them.
- Use [[wikilinks]] in your chat responses too! They become clickable links
  that open the wiki page. Whenever you mention a wiki page, link to it."""


_system_prompt_cache: dict = {"prompt": None, "schema_mtime": 0, "soul_mtime": 0}


def _build_system_prompt() -> str:
    schema_mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    soul_mtime = SOUL_PATH.stat().st_mtime if SOUL_PATH.exists() else 0

    if (_system_prompt_cache["prompt"]
            and _system_prompt_cache["schema_mtime"] == schema_mtime
            and _system_prompt_cache["soul_mtime"] == soul_mtime):
        return _system_prompt_cache["prompt"]

    schema = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
    soul = SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else ""
    prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema, soul=soul, tools=TOOL_SCHEMA_BLOCK)
    _system_prompt_cache.update(prompt=prompt, schema_mtime=schema_mtime, soul_mtime=soul_mtime)
    return prompt


def _extract_tool_call(text: str) -> dict | None:
    """Extract a tool call JSON block from ```tool fences."""
    match = re.search(r"```tool\s*\n(.*?)\n```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


async def chat_stream(
    message: str, history: list[dict], model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Run the chat loop with prompt-based tool calling. Yields SSE events."""
    active_model = model or MODEL
    system = _build_system_prompt()

    messages = [{"role": "system", "content": system}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})

    _log_event("chat_start", model=active_model, message_len=len(message), history_len=len(history))

    round_num = 0
    empty_retries = 0
    while round_num < _MAX_TOOL_ROUNDS:
        # Throttle outgoing requests to avoid upstream rate limits
        throttle_wait = await _wait_for_capacity()
        if throttle_wait > 0:
            _log_event("throttle", wait_s=round(throttle_wait, 1))
            yield {"event": "text", "data": f"(pacing requests, resuming in {throttle_wait:.0f}s...)"}

        # Collect full response (non-streaming for tool detection)
        # Retry on rate limits with exponential backoff
        _log_event("llm_request", round=round_num + 1, model=active_model)
        for attempt in range(4):
            try:
                response = await client.chat.completions.create(
                    model=active_model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=4096,
                )
                break
            except RateLimitError:
                if attempt == 3:
                    _log_event("rate_limit_exhausted", round=round_num + 1)
                    yield {"event": "text", "data": "Rate limited by upstream provider. The free tier has strict limits — try again in a minute, or set a different model via LLM_MODEL."}
                    return
                wait = 2 ** attempt
                _log_event("rate_limit_retry", attempt=attempt + 1, wait_s=wait)
                yield {"event": "text", "data": f"(rate limited, retrying in {wait}s...)"}
                await asyncio.sleep(wait)

        if not response.choices:
            _log_event("empty_response", round=round_num + 1)
            yield {"event": "text", "data": "Got an empty response from the model — try again or pick a different one."}
            return

        assistant_text = response.choices[0].message.content or ""
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        usage = response.usage
        _log_event("llm_response", round=round_num + 1,
                   text_len=len(assistant_text),
                   finish_reason=finish_reason,
                   prompt_tokens=getattr(usage, "prompt_tokens", None),
                   completion_tokens=getattr(usage, "completion_tokens", None))

        # Empty content with zero tokens means the model returned nothing useful.
        # Retry a couple times, then fall back to the default model.
        if not assistant_text.strip():
            empty_retries += 1
            if empty_retries <= 2:
                wait = empty_retries
                _log_event("empty_text_retry", round=round_num + 1, attempt=empty_retries, wait_s=wait)
                await asyncio.sleep(wait)
                round_num += 1
                continue
            # Try falling back to default model if we aren't already on it
            if active_model != MODEL:
                _log_event("model_fallback", from_model=active_model, to_model=MODEL)
                yield {"event": "text", "data": f"(model {active_model} not responding, falling back to {MODEL}...)"}
                active_model = MODEL
                empty_retries = 0
                round_num += 1
                continue
            _log_event("empty_text_exhausted", round=round_num + 1)
            yield {"event": "text", "data": "The model keeps returning empty responses — try again or switch models."}
            return

        # Got a real response -- reset empty retry counter
        empty_retries = 0

        tool_call = _extract_tool_call(assistant_text)

        if tool_call is None:
            # No tool call — this is the final response. Stream it out.
            _log_event("chat_end", rounds=round_num + 1, reason="final_response")
            yield {"event": "text", "data": assistant_text}
            return

        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})

        _log_event("tool_call", round=round_num + 1, tool=tool_name,
                   args={k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v)
                         for k, v in tool_args.items()})

        yield {"event": "tool_call", "data": json.dumps({"tool": tool_name, "args": tool_args})}

        # Execute the tool
        result = execute_tool(tool_name, tool_args)
        _log_event("tool_result", round=round_num + 1, tool=tool_name, result_len=len(result))

        # Notify about wiki updates
        if tool_name == "write_wiki":
            page = tool_args.get("page", "")
            yield {"event": "wiki_update", "data": page}

        yield {"event": "tool_result", "data": json.dumps({"tool": tool_name, "result": result})}

        # Add assistant message and tool result to conversation
        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({
            "role": "user",
            "content": f"Tool result for {tool_name}:\n{result}",
        })

        round_num += 1

    _log_event("chat_end", rounds=round_num, reason="tool_round_limit")
    yield {
        "event": "text",
        "data": (
            f"Stopped after {_MAX_TOOL_ROUNDS} tool round(s). "
            "The request needs a tighter prompt or manual follow-up."
        ),
    }
