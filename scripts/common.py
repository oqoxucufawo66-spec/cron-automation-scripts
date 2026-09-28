"""Shared helpers: .env loading, logging, Telegram delivery, formatting."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TELEGRAM_LIMIT = 4096

logger = logging.getLogger("scripts")


def load_env() -> None:
    """Load ``.env`` from the project root (cron runs with an empty environment)."""
    load_dotenv(PROJECT_ROOT / ".env")


def setup_logging(verbose: bool = False) -> None:
    level_name = "DEBUG" if verbose else os.getenv("LOG_LEVEL", "INFO").upper()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_file = os.getenv("LOG_FILE")
    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=level_name,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def resolve_path(value: str | Path) -> Path:
    """Relative paths are resolved against the project root, not the cron CWD."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024 or unit == "TB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    raise AssertionError("unreachable")


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split long text into Telegram-sized chunks, preferring line breaks."""
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str, *, parse_mode: str | None = "HTML", timeout: float = 15) -> None:
    """Send a message via Telegram Bot API. Raises on failure."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        payload: dict[str, str | bool] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        response = requests.post(url, json=payload, timeout=timeout)
        if not response.ok:
            # Do not log the URL: it contains the bot token.
            raise RuntimeError(f"Telegram API error {response.status_code}: {response.text[:200]}")
    logger.info("Telegram message sent (%d chars)", len(text))
