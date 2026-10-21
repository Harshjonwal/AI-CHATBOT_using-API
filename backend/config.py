"""App settings, read once from the environment (and an optional .env file)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Get a key at https://aistudio.google.com/apikey (the SDK also reads GOOGLE_API_KEY).
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# An alias for Google's newest Flash-Lite model: fast, with the most generous free tier.
# Set GEMINI_MODEL=gemini-flash-latest for a stronger (but slower, more limited) model.
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

# Give up on a request after this long, so the UI shows an error instead of waiting forever.
TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))

# Thinking tokens count toward this limit, so leave room for them as well as the reply.
MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "32768"))

# How many tokens the model may spend thinking before it answers, per level.
THINKING_BUDGETS = {"low": 1024, "medium": 4096, "high": 16384}
EFFORT_LEVELS = list(THINKING_BUDGETS)
DEFAULT_EFFORT = os.getenv("GEMINI_DEFAULT_THINKING", "low")

DATA_DIR = Path(os.getenv("CHATBOT_DATA_DIR", ROOT_DIR / "data"))
FRONTEND_DIR = ROOT_DIR / "frontend"

DEFAULT_PERSONA = (
    "You are a friendly, knowledgeable assistant. Answer clearly and directly, "
    "use Markdown when it helps (lists, tables, fenced code blocks with a language tag), "
    "and keep answers as short as the question allows. If you are unsure, say so."
)
