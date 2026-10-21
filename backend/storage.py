"""Conversation persistence: one JSON file per conversation under data/."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_usage() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "web_search_requests": 0,
    }


class ConversationStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "conversations"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, conv_id: str) -> Path:
        # Ids come from URLs, so validate them before they touch the filesystem.
        if not _ID_RE.match(conv_id):
            raise KeyError(conv_id)
        return self.dir / f"{conv_id}.json"

    def create(self, *, persona: str, system: str, web_search: bool, effort: str) -> dict:
        ts = now_iso()
        conv = {
            "id": uuid.uuid4().hex,
            "title": "New chat",
            "persona": persona,
            # The system prompt and tool set are frozen at creation: editing either
            # later would invalidate the thinking blocks already in the history.
            "system": system,
            "web_search": web_search,
            "effort": effort,
            "created_at": ts,
            "updated_at": ts,
            "messages": [],
            "usage": empty_usage(),
        }
        self.save(conv)
        return conv

    def get(self, conv_id: str) -> dict | None:
        try:
            path = self._path(conv_id)
        except KeyError:
            return None
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, conv: dict) -> None:
        conv["updated_at"] = now_iso()
        path = self._path(conv["id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
        # Write-then-rename so a crash never leaves a half-written file. Sync tools
        # such as OneDrive can briefly lock the target on Windows, so retry a little.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def delete(self, conv_id: str) -> bool:
        try:
            path = self._path(conv_id)
        except KeyError:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True

    def list(self) -> list[dict]:
        items = []
        for path in self.dir.glob("*.json"):
            try:
                conv = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append(
                {
                    "id": conv["id"],
                    "title": conv["title"],
                    "updated_at": conv["updated_at"],
                    "message_count": sum(1 for m in conv["messages"] if m["role"] == "user"),
                }
            )
        items.sort(key=lambda c: c["updated_at"], reverse=True)
        return items
