"""Talking to Gemini: request building, streaming, and conversation-history bookkeeping.

History rules this module follows:
  * The history only grows. The one removal is rolling back the turn in flight when
    it fails or is blocked, so a failed message never lingers in the chat.
  * Each message is stored as {"role": "user" | "model", "parts": [{"text": ...}]},
    the shape the Gemini API uses, plus a `_meta` dict of our own (never sent).
  * The system prompt and web-search setting are fixed when the chat is created.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import date

import httpx
from google import genai
from google.genai import errors, types

from . import config
from .storage import ConversationStore, empty_usage

log = logging.getLogger("chatbot")

# Finish reasons that mean Gemini withheld the answer rather than completing it.
BLOCKED_FINISH_REASONS = {
    "SAFETY", "RECITATION", "LANGUAGE", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
}


def build_system_prompt(persona: str, web_search: bool) -> str:
    parts = [persona.strip() or config.DEFAULT_PERSONA, f"Today's date is {date.today().isoformat()}."]
    if web_search:
        parts.append(
            "You can search Google. Use it for current events, prices, or anything that may "
            "have changed recently; answer from your own knowledge otherwise."
        )
    return "\n\n".join(parts)


def make_title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= 48 else line[:47].rstrip() + "…"


def message_text(message: dict) -> str:
    return "".join(p.get("text", "") for p in message["parts"])


def to_display(messages: list[dict]) -> list[dict]:
    """Convert stored history into the chat bubbles the UI renders."""
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "text": message_text(m)})
            continue
        meta = m.get("_meta", {})
        out.append(
            {
                "role": "assistant",
                "text": message_text(m),
                "thinking": meta.get("thinking", ""),
                "searches": meta.get("searches", []),
                "sources": meta.get("sources", []),
                "search_html": meta.get("search_html"),
                "meta": {k: meta[k] for k in ("usage", "model", "effort", "finish_reason", "stopped") if k in meta},
            }
        )
    return out


def describe_error(exc: Exception, web_search: bool = False) -> str:
    if isinstance(exc, errors.APIError):
        code, message = exc.code or 0, exc.message or str(exc)
        if "api key" in message.lower() and code in (400, 401, 403):
            return "Your Gemini API key isn't valid. Check GEMINI_API_KEY in your .env file."
        if code in (401, 403):
            return "This API key isn't allowed to use that model. Check the key in Google AI Studio."
        if code == 404:
            return f"Model '{config.MODEL}' wasn't found. Check GEMINI_MODEL in your .env file."
        if code == 429 and web_search:
            return (
                "Gemini refused this because of a quota limit. Google Search is often not included "
                "in the free tier, so start a new chat with Google Search turned off."
            )
        if code == 429:
            return "You've hit Gemini's rate limit or free-tier quota. Wait a minute and try again."
        if code == 504:
            return (
                f"Gemini took too long to answer, so '{config.MODEL}' may be overloaded. "
                "Try again, or set GEMINI_MODEL in .env to a different model."
            )
        if code >= 500:
            return "Gemini is busy or had a server error. Try again in a moment."
        return f"Gemini rejected the request: {message}"
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Gemini didn't respond within {config.TIMEOUT_SECONDS} seconds, so '{config.MODEL}' may be "
            "overloaded. Try again, or set GEMINI_MODEL in .env to a different model."
        )
    if isinstance(exc, httpx.TransportError):
        return "Couldn't reach the Gemini API. Check your internet connection."
    if "No API key was provided" in str(exc):
        return "No API key configured. Add GEMINI_API_KEY to your .env file and restart the server."
    return f"Unexpected error: {exc}"


def _name(value) -> str | None:
    """Enum values from the SDK -> plain strings like "STOP"."""
    return None if value is None else getattr(value, "value", str(value))


@dataclass
class _StreamState:
    """Everything gathered from the chunks of one streamed reply."""

    text: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    search_html: str | None = None
    usage: types.GenerateContentResponseUsageMetadata | None = None
    finish_reason: str | None = None
    block_reason: str | None = None


def translate_chunk(chunk: types.GenerateContentResponse, state: _StreamState) -> list[dict]:
    """Record one streamed chunk in `state` and return the UI events it produces."""
    events: list[dict] = []
    if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
        state.block_reason = _name(chunk.prompt_feedback.block_reason)
    if chunk.usage_metadata:
        state.usage = chunk.usage_metadata  # running totals; the last chunk has the final numbers
    if not chunk.candidates:
        return events
    candidate = chunk.candidates[0]
    if candidate.finish_reason:
        state.finish_reason = _name(candidate.finish_reason)
    for part in (candidate.content.parts if candidate.content and candidate.content.parts else []):
        if not part.text:
            continue
        if part.thought:  # a summary of the model's reasoning, shown in the "Thought process" panel
            state.thinking.append(part.text)
            events.append({"type": "thinking", "text": part.text})
        else:
            state.text.append(part.text)
            events.append({"type": "text", "text": part.text})
    grounding = candidate.grounding_metadata
    if grounding:
        for query in grounding.web_search_queries or []:
            if query not in state.queries:
                state.queries.append(query)
                events.append({"type": "search", "query": query})
        for source in grounding.grounding_chunks or []:
            if source.web and source.web.uri:
                state.sources.setdefault(source.web.uri, source.web.title or source.web.domain or source.web.uri)
        if grounding.search_entry_point and grounding.search_entry_point.rendered_content:
            state.search_html = grounding.search_entry_point.rendered_content
    return events


def usage_dict(state: _StreamState) -> dict:
    usage = empty_usage()
    u = state.usage
    if u is not None:
        cached = u.cached_content_token_count or 0
        usage["input_tokens"] = max((u.prompt_token_count or 0) - cached, 0)
        usage["cache_read_input_tokens"] = cached
        usage["output_tokens"] = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
    usage["web_search_requests"] = len(state.queries)
    return usage


class ChatService:
    def __init__(self, store: ConversationStore, client: genai.Client | None = None):
        self.store = store
        self._client = client
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def client(self) -> genai.Client:
        # Created lazily so the server starts (and shows a helpful error) without a key.
        if self._client is None:
            self._client = genai.Client(
                api_key=config.API_KEY,
                # The SDK's default is no timeout at all; its unit is milliseconds.
                http_options=types.HttpOptions(timeout=config.TIMEOUT_SECONDS * 1000),
            )
        return self._client

    def is_busy(self, conv_id: str) -> bool:
        lock = self._locks.get(conv_id)
        return bool(lock and lock.locked())

    def request_kwargs(self, conv: dict, effort: str) -> dict:
        generation_config = types.GenerateContentConfig(
            system_instruction=conv["system"],
            max_output_tokens=config.MAX_TOKENS,
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=config.THINKING_BUDGETS[effort],
            ),
            tools=[types.Tool(google_search=types.GoogleSearch())] if conv["web_search"] else None,
        )
        # `_meta` is ours; the API only gets role and parts.
        contents = [
            types.Content(role=m["role"], parts=[types.Part(text=p["text"]) for p in m["parts"]])
            for m in conv["messages"]
        ]
        return {"model": config.MODEL, "contents": contents, "config": generation_config}

    async def send(self, conv_id: str, user_text: str, effort: str) -> AsyncIterator[dict]:
        """Stream one turn as UI events. Turns in the same conversation run one at a time."""
        lock = self._locks.setdefault(conv_id, asyncio.Lock())
        async with lock:
            conv = self.store.get(conv_id)
            if conv is None:
                yield {"type": "error", "message": "Conversation not found."}
                return
            # aclosing() makes the inner generator's cleanup run now, inside the lock,
            # even when the client disconnects mid-stream.
            async with aclosing(self._run_turn(conv, user_text, effort)) as events:
                async for event in events:
                    yield event

    async def _run_turn(self, conv: dict, user_text: str, effort: str) -> AsyncIterator[dict]:
        history = conv["messages"]
        start = len(history)
        history.append({"role": "user", "parts": [{"text": user_text}]})
        if conv["title"] == "New chat":
            conv["title"] = make_title(user_text)
        conv["effort"] = effort

        state = _StreamState()
        finished = False
        try:
            yield {"type": "start", "title": conv["title"]}
            stream = await self.client.aio.models.generate_content_stream(**self.request_kwargs(conv, effort))
            async with aclosing(stream):  # closes the connection promptly if the user hits Stop
                async for chunk in stream:
                    for ui_event in translate_chunk(chunk, state):
                        yield ui_event

            usage = usage_dict(state)
            log.info(
                "reply conv=%s model=%s finish=%s in=%s out=%s cached=%s",
                conv["id"], config.MODEL, state.finish_reason,
                usage["input_tokens"], usage["output_tokens"], usage["cache_read_input_tokens"],
            )

            # A blocked prompt or answer is rolled back so it doesn't stay in the history.
            blocked = state.block_reason or (
                state.finish_reason if state.finish_reason in BLOCKED_FINISH_REASONS else None
            )
            if blocked:
                del history[start:]
                finished = True
                yield {"type": "blocked", "reason": blocked}
                return

            text = "".join(state.text)
            if not text.strip():
                del history[start:]
                finished = True
                why = (
                    "it used up its output tokens while thinking"
                    if state.finish_reason == "MAX_TOKENS"
                    else "the response was empty"
                )
                yield {"type": "error", "message": f"Gemini didn't produce an answer ({why}). Please try again."}
                return

            sources = [{"url": u, "title": t} for u, t in state.sources.items()]
            meta = {"usage": usage, "model": config.MODEL, "effort": effort, "finish_reason": state.finish_reason}
            history.append(
                {
                    "role": "model",
                    "parts": [{"text": text}],
                    "_meta": {
                        **meta,
                        "thinking": "".join(state.thinking),
                        "searches": state.queries,
                        "sources": sources,
                        "search_html": state.search_html,
                    },
                }
            )
            for key, value in usage.items():
                conv["usage"][key] += value
            finished = True
            yield {
                "type": "done",
                **meta,
                "sources": sources,
                "search_html": state.search_html,
                "conversation_usage": conv["usage"],
            }
        except Exception as exc:  # API errors, network trouble, a missing key, ...
            if isinstance(exc, errors.APIError):
                log.warning("Gemini API error conv=%s: %s", conv["id"], exc)
            else:
                log.exception("Error conv=%s", conv["id"])
            del history[start:]
            finished = True
            yield {"type": "error", "message": describe_error(exc, web_search=conv["web_search"])}
        finally:
            if not finished:
                # The client went away mid-reply (Stop button, closed tab). Keep the text
                # the user already saw; drop the turn entirely if there was none.
                partial = "".join(state.text).strip()
                if partial:
                    usage = usage_dict(state)
                    history.append(
                        {
                            "role": "model",
                            "parts": [{"text": partial}],
                            "_meta": {
                                "usage": usage, "model": config.MODEL, "effort": effort, "stopped": True,
                                "thinking": "".join(state.thinking), "searches": state.queries,
                            },
                        }
                    )
                    for key, value in usage.items():
                        conv["usage"][key] += value
                else:
                    del history[start:]
            self.store.save(conv)
