# ChatBot (Google Gemini API)

A web chatbot built with Python, FastAPI, and Google's official Gen AI SDK (`google-genai`). Replies stream into a browser chat UI, and conversations are saved locally.

## Features

- **Streaming replies** sent over Server-Sent Events, with a Stop button.
- **Multi-turn memory**: each chat sends its full history to the model.
- **Saved conversations** as JSON in `data/`, with a sidebar to switch, rename, delete, or export a chat as Markdown.
- **Per-chat persona** (system prompt) and optional **Google Search grounding** that shows its sources.
- **Thinking selector** (`low` / `medium` / `high`): how much the model reasons before answering.
- **Thought process panel** showing Gemini's summarized reasoning.
- **Error handling**: friendly messages for a bad key, free-tier rate limits, server trouble, or network problems, with a one-click Retry.
- **Safety blocks**: when Gemini's filters block a prompt or answer, the UI says so and the message isn't saved.
- **Markdown rendering** with syntax-highlighted code blocks and copy buttons.
- **Light and dark themes**, and a layout that works on phones.

## Setup (Windows)

1. Get a free API key at https://aistudio.google.com/apikey (sign in with a Google account, then **Create API key**).
2. Open `.env` and paste the key after `GEMINI_API_KEY=`, then save.

If you're starting from a fresh copy of the project, run these first:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
copy .env.example .env
```

## Run

```bash
.venv\Scripts\python run.py
```

Then open http://127.0.0.1:8000.

### From VS Code

Open the folder in VS Code and install the **Python** extension if prompted. Then press **F5** and choose **Run ChatBot**. The `.vscode/` folder already points VS Code at the `.venv` interpreter. Stop the server with **Shift+F5**, and restart it after editing `.env`.

## Tests

The tests use a fake Gemini client, so they don't need an API key.

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

```bash
.venv\Scripts\python -m pytest
```

## Project layout

```
backend/
  config.py    settings from .env (key, model, thinking levels, limits)
  storage.py   one JSON file per conversation, written atomically
  chat.py      Gemini requests, streaming, history rules, error mapping
  main.py      FastAPI routes + SSE endpoint, serves the frontend
frontend/
  index.html, styles.css, app.js   the chat UI (no build step)
tests/         pytest suite with a fake client
run.py         starts the server on 127.0.0.1:8000
```

## How it works

1. The browser POSTs a message to `/api/conversations/{id}/messages`.
2. `chat.py` sends the conversation history to Gemini with `client.aio.models.generate_content_stream(...)`.
3. Each streamed chunk is split into thought summaries, answer text, and search info. These are forwarded to the browser as small JSON events.
4. When the reply finishes, it's saved to the chat's history file.

The history only grows. If a turn fails or is blocked, only that turn is rolled back. When Google Search is used, the UI also shows Google's search-suggestion chips, as Google's terms require. They render in a sandboxed frame.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | (none) | Your API key (required) |
| `GEMINI_MODEL` | `gemini-flash-lite-latest` | Model ID. The default is Google's newest Flash-Lite: fast, with the most generous free tier. `gemini-flash-latest` is stronger but slower and more limited. |
| `GEMINI_TIMEOUT_SECONDS` | `120` | How long to wait for Gemini before showing an error |
| `GEMINI_DEFAULT_THINKING` | `low` | Default thinking level for new chats |
| `GEMINI_MAX_TOKENS` | `32768` | Max output tokens per reply, thinking included |
| `PORT` | `8000` | Server port |

The free tier has per-minute and per-day request limits. If you hit them, the chat says so; wait a minute and retry. Google Search grounding is often not included in the free tier. If a chat with Google Search on gets a quota error, start a new chat with it off.

## Ideas for next steps

- Add function calling so the bot can use your own tools, such as a calculator or your own data.
- Add image or PDF uploads. Gemini can read both.
- Add user accounts and a database instead of JSON files, if several people will use it.
