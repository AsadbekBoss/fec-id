"""Structured JSON logging with mandatory redaction (DEVELOPMENT_RULES §6.1)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Patterns that must never appear in logs.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # credentials inside URLs: rtsp://user:pass@host, postgresql://user:pass@host
    (
        re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"),
        r"\g<scheme>***:***@",
    ),
    # Telegram bot tokens inside Bot API URLs: https://api.telegram.org/bot123456:ABC.../sendMessage
    (re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"), "bot***"),
    # bearer tokens / JWTs
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "***jwt***"),
    # key=value secrets
    (
        re.compile(r"(?i)(password|passwd|secret|token|api_key)(\s*[=:]\s*)([^\s,;&]+)"),
        r"\1\2***",
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class JsonRedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonRedactingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
