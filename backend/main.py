"""FastAPI app: JSON endpoints for conversations plus a Server-Sent Events chat stream."""

from __future__ import annotations

import json
import logging
from contextlib import aclosing
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .chat import ChatService, build_system_prompt, to_display
from .storage import ConversationStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# How much the model thinks before answering (see THINKING_BUDGETS in config.py).
Effort = Literal["low", "medium", "high"]

app = FastAPI(title="ChatBot")
store = ConversationStore(config.DATA_DIR)
service = ChatService(store)


class NewConversation(BaseModel):
    persona: str = Field(default="", max_length=8000)
    web_search: bool = False
    effort: Effort = config.DEFAULT_EFFORT


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    effort: Effort | None = None


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    effort: Effort | None = None


def _get_or_404(conv_id: str) -> dict:
    conv = store.get(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


def _public(conv: dict) -> dict:
    return {
        "id": conv["id"],
        "title": conv["title"],
        "persona": conv["persona"],
        "web_search": conv["web_search"],
        "effort": conv["effort"],
        "created_at": conv["created_at"],
        "updated_at": conv["updated_at"],
        "usage": conv["usage"],
        "messages": to_display(conv["messages"]),
    }


@app.get("/api/config")
def get_config() -> dict:
    return {
        "model": config.MODEL,
        "efforts": config.EFFORT_LEVELS,
        "default_effort": config.DEFAULT_EFFORT,
        "default_persona": config.DEFAULT_PERSONA,
        "has_api_key": bool(config.API_KEY),
    }


@app.get("/api/conversations")
def list_conversations() -> list[dict]:
    return store.list()


@app.post("/api/conversations", status_code=201)
def create_conversation(body: NewConversation) -> dict:
    conv = store.create(
        persona=body.persona.strip(),
        system=build_system_prompt(body.persona, body.web_search),
        web_search=body.web_search,
        effort=body.effort,
    )
    return _public(conv)


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: str) -> dict:
    return _public(_get_or_404(conv_id))


@app.patch("/api/conversations/{conv_id}")
def update_conversation(conv_id: str, body: ConversationUpdate) -> dict:
    if service.is_busy(conv_id):
        raise HTTPException(status_code=409, detail="A reply is still streaming")
    conv = _get_or_404(conv_id)
    if body.title is not None:
        conv["title"] = body.title.strip()
    if body.effort is not None:
        conv["effort"] = body.effort
    store.save(conv)
    return _public(conv)


@app.delete("/api/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: str) -> Response:
    if service.is_busy(conv_id):
        raise HTTPException(status_code=409, detail="A reply is still streaming")
    if not store.delete(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@app.get("/api/conversations/{conv_id}/export")
def export_conversation(conv_id: str) -> Response:
    conv = _get_or_404(conv_id)
    lines = [f"# {conv['title']}", ""]
    for msg in to_display(conv["messages"]):
        lines += [f"## {'You' if msg['role'] == 'user' else 'Assistant'}", "", msg["text"], ""]
        for src in msg.get("sources", []):
            lines.append(f"- [{src['title']}]({src['url']})")
        if msg.get("sources"):
            lines.append("")
    filename = "".join(c if c.isalnum() or c in " -_" else "_" for c in conv["title"]).strip() or "chat"
    return Response(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )


@app.post("/api/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: NewMessage) -> StreamingResponse:
    conv = _get_or_404(conv_id)
    if service.is_busy(conv_id):
        raise HTTPException(status_code=409, detail="A reply is already streaming in this chat")
    effort = body.effort or conv["effort"]

    async def event_stream():
        async with aclosing(service.send(conv_id, body.content, effort)) as events:
            async for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Serve the chat UI last so the /api routes above take precedence.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
