"""Tests run against a fake Gemini client, so they need no API key and cost nothing."""

import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from backend import main
from backend.chat import ChatService, describe_error, to_display, translate_chunk, _StreamState
from backend.storage import ConversationStore


# ---------- Fake SDK objects ----------

def chunk(*parts, finish=None, grounding=None, usage=None, block=None):
    """Build a fake streamed GenerateContentResponse chunk."""
    candidate = NS(
        content=NS(parts=[NS(text=t, thought=thought) for t, thought in parts]),
        finish_reason=finish,
        grounding_metadata=grounding,
    )
    return NS(
        candidates=None if block else [candidate],
        usage_metadata=usage,
        prompt_feedback=NS(block_reason=block) if block else None,
    )


USAGE = NS(prompt_token_count=20, cached_content_token_count=5, candidates_token_count=8, thoughts_token_count=4)


def reply(*words, finish="STOP"):
    chunks = [chunk(("Let me think.", True))]
    chunks += [chunk((w, False)) for w in words]
    chunks.append(chunk(finish=finish, usage=USAGE))
    return chunks


class FakeClient:
    """Stands in for genai.Client: each request plays the next scripted reply."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.aio = NS(models=NS(generate_content_stream=self._stream))

    async def _stream(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item

        async def gen():
            for c in item:
                yield c

        return gen()


def run(service, conv_id, text, effort="low"):
    async def collect():
        return [e async for e in service.send(conv_id, text, effort)]

    return asyncio.run(collect())


def new_conv(store, web_search=False):
    return store.create(persona="", system="Be brief.", web_search=web_search, effort="low")


def api_error(code, message):
    return errors.ClientError(code, {"error": {"code": code, "message": message, "status": "X"}})


# ---------- Pure helpers ----------

def test_translate_chunk_separates_thoughts_text_and_search():
    state = _StreamState()
    grounding = NS(
        web_search_queries=["paris weather"],
        grounding_chunks=[NS(web=NS(uri="https://w.example", title="Weather", domain="w.example"))],
        search_entry_point=NS(rendered_content="<div>chips</div>"),
    )
    events = translate_chunk(chunk(("hmm", True), ("Sunny", False), grounding=grounding), state)
    assert events == [
        {"type": "thinking", "text": "hmm"},
        {"type": "text", "text": "Sunny"},
        {"type": "search", "query": "paris weather"},
    ]
    assert state.sources == {"https://w.example": "Weather"}
    assert state.search_html == "<div>chips</div>"


def test_error_messages_are_friendly():
    assert "isn't valid" in describe_error(api_error(400, "API key not valid. Please pass a valid API key."))
    assert "quota" in describe_error(api_error(429, "Resource exhausted"))
    assert "Google Search turned off" in describe_error(api_error(429, "quota"), web_search=True)
    assert "overloaded" in describe_error(api_error(504, "Deadline expired"))
    assert "overloaded" in describe_error(httpx.ReadTimeout("slow"))
    assert "GEMINI_MODEL" in describe_error(api_error(404, "models/x is not found"))
    assert "internet" in describe_error(httpx.ConnectError("boom"))
    assert "GEMINI_API_KEY" in describe_error(ValueError("No API key was provided. Please pass a valid API key."))


def test_display_uses_meta_for_extras():
    msgs = [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "hello"}], "_meta": {"thinking": "t", "effort": "low", "sources": []}},
    ]
    out = to_display(msgs)
    assert out[1]["text"] == "hello" and out[1]["thinking"] == "t" and out[1]["meta"] == {"effort": "low"}


# ---------- ChatService ----------

def test_turn_streams_and_appends_to_history(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store)
    client = FakeClient(reply("Hello", " there"), reply("Again"))
    service = ChatService(store, client)

    events = run(service, conv["id"], "Hi!")
    assert [e["type"] for e in events] == ["start", "thinking", "text", "text", "done"]
    saved = store.get(conv["id"])
    assert saved["messages"][1]["parts"] == [{"text": "Hello there"}]
    assert saved["messages"][1]["_meta"]["thinking"] == "Let me think."
    assert saved["title"] == "Hi!"
    assert saved["usage"]["input_tokens"] == 15 and saved["usage"]["cache_read_input_tokens"] == 5
    assert saved["usage"]["output_tokens"] == 12

    run(service, conv["id"], "More", effort="high")
    second = client.calls[1]
    # The earlier turn is replayed as role + text only; our `_meta` never reaches the API.
    assert [(c.role, c.parts[0].text) for c in second["contents"]] == [
        ("user", "Hi!"), ("model", "Hello there"), ("user", "More"),
    ]
    cfg = second["config"]
    assert cfg.system_instruction == "Be brief."
    assert cfg.thinking_config.thinking_budget == 16384 and cfg.thinking_config.include_thoughts
    assert cfg.tools is None


def test_web_search_chat_declares_google_search(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store, web_search=True)
    client = FakeClient(reply("ok"))
    run(ChatService(store, client), conv["id"], "news?")
    assert client.calls[0]["config"].tools[0].google_search is not None


def test_blocked_prompt_rolls_back_the_turn(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store)
    client = FakeClient([chunk(block="SAFETY")])
    events = run(ChatService(store, client), conv["id"], "something")
    assert events[-1] == {"type": "blocked", "reason": "SAFETY"}
    assert store.get(conv["id"])["messages"] == []


def test_blocked_answer_rolls_back_the_turn(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store)
    client = FakeClient(reply("partial", finish="SAFETY"))
    events = run(ChatService(store, client), conv["id"], "something")
    assert events[-1]["type"] == "blocked"
    assert store.get(conv["id"])["messages"] == []


def test_empty_answer_is_an_error(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store)
    client = FakeClient(reply(finish="MAX_TOKENS"))
    events = run(ChatService(store, client), conv["id"], "hard question")
    assert events[-1]["type"] == "error" and "output tokens" in events[-1]["message"]
    assert store.get(conv["id"])["messages"] == []


def test_api_error_rolls_back_and_reports(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store)
    client = FakeClient(api_error(429, "Resource exhausted"))
    events = run(ChatService(store, client), conv["id"], "hello")
    assert events[-1]["type"] == "error" and "quota" in events[-1]["message"]
    assert store.get(conv["id"])["messages"] == []


def test_stop_keeps_partial_text(tmp_path):
    store = ConversationStore(tmp_path)
    conv = new_conv(store)
    service = ChatService(store, FakeClient(reply("one", " two", " three")))

    async def stop_after_first_text():
        agen = service.send(conv["id"], "count", "low")
        async for event in agen:
            if event["type"] == "text":
                break
        await agen.aclose()  # what the server does when the browser disconnects

    asyncio.run(stop_after_first_text())
    msgs = store.get(conv["id"])["messages"]
    assert msgs[-1]["parts"] == [{"text": "one"}]
    assert msgs[-1]["_meta"]["stopped"] is True


# ---------- HTTP API ----------

@pytest.fixture
def api(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    fake = FakeClient(reply("Hi", " from", " Gemini"))
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "service", ChatService(store, fake))
    with TestClient(main.app) as client:
        yield client


def sse_events(body: str):
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def test_http_flow(api):
    assert api.get("/api/config").json()["efforts"] == ["low", "medium", "high"]
    conv = api.post("/api/conversations", json={"persona": "Pirate", "web_search": False}).json()
    assert conv["persona"] == "Pirate" and conv["messages"] == []

    res = api.post(f"/api/conversations/{conv['id']}/messages", json={"content": "Hello"})
    assert res.headers["content-type"].startswith("text/event-stream")
    assert sse_events(res.text)[-1]["type"] == "done"

    full = api.get(f"/api/conversations/{conv['id']}").json()
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert full["messages"][1]["text"] == "Hi from Gemini"

    assert api.patch(f"/api/conversations/{conv['id']}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert api.get("/api/conversations").json()[0]["title"] == "Renamed"
    export = api.get(f"/api/conversations/{conv['id']}/export")
    assert "## Assistant" in export.text and "attachment" in export.headers["content-disposition"]

    assert api.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert api.get(f"/api/conversations/{conv['id']}").status_code == 404


def test_http_validation(api):
    assert api.get("/api/conversations/../../etc").status_code == 404
    assert api.post("/api/conversations", json={"effort": "max"}).status_code == 422
    conv = api.post("/api/conversations", json={}).json()
    assert api.post(f"/api/conversations/{conv['id']}/messages", json={"content": ""}).status_code == 422
    assert api.get("/").status_code == 200  # the UI is served
