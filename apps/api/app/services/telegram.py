"""Minimal Telegram Bot API client (messages with optional inline buttons, edits, button
answers, getUpdates, getMe, the command list).

The token is part of the request URL by Telegram's design, so URLs and httpx errors are never
logged here (they would contain it). Tests replace the client through `telegram_client`.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.core.http import tls_context


class TelegramError(Exception):
    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked  # the chat blocked the bot or was deleted: stop sending to it


# Inline keyboard: rows of (label, callback data <= 64 bytes).
Keyboard = list[list[tuple[str, str]]]


class Telegram(Protocol):
    async def send(self, chat_id: int, html: str, keyboard: Keyboard | None = None) -> None: ...
    async def edit(
        self, chat_id: int, message_id: int, html: str, keyboard: Keyboard | None = None
    ) -> None: ...
    async def answer(self, callback_id: str, text: str | None = None) -> None: ...
    async def updates(self, offset: int | None, poll_s: int) -> list[dict[str, Any]]: ...
    async def me(self) -> dict[str, Any]: ...
    async def set_commands(self, commands: list[tuple[str, str]]) -> None: ...


def _markup(keyboard: Keyboard | None) -> dict[str, Any]:
    if not keyboard:
        return {}
    rows = [[{"text": t, "callback_data": d} for t, d in row] for row in keyboard]
    return {"reply_markup": {"inline_keyboard": rows}}


class TelegramClient:
    def __init__(self, token: str, base_url: str) -> None:
        self._base = f"{base_url.rstrip('/')}/bot{token}"

    async def _call(self, method: str, payload: dict[str, Any], wait: float = 0) -> Any:
        try:
            async with httpx.AsyncClient(timeout=15 + wait, verify=tls_context()) as c:
                r = await c.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as e:
            raise TelegramError(f"Telegram is unreachable ({type(e).__name__})") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 200 and body.get("ok"):
            return body.get("result")
        desc = str(body.get("description") or f"HTTP {r.status_code}")[:200]
        if r.status_code == 401:
            raise TelegramError("The bot token was rejected (check TELEGRAM_BOT_TOKEN)")
        raise TelegramError(desc, blocked=r.status_code in (400, 403) and "chat" in desc.lower())

    async def send(self, chat_id: int, html: str, keyboard: Keyboard | None = None) -> None:
        await self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": html[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                **_markup(keyboard),
            },
        )

    async def edit(
        self, chat_id: int, message_id: int, html: str, keyboard: Keyboard | None = None
    ) -> None:
        try:
            await self._call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": html[:4096],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    **_markup(keyboard),
                },
            )
        except TelegramError as e:
            if "not modified" not in str(e):  # same view pressed twice: nothing to do
                raise

    async def answer(self, callback_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:200]
        await self._call("answerCallbackQuery", payload)

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        await self._call(
            "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]}
        )

    async def updates(self, offset: int | None, poll_s: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": poll_s,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, wait=poll_s)
        return list(result or [])

    async def me(self) -> dict[str, Any]:
        return dict(await self._call("getMe", {}))


def telegram_client() -> Telegram | None:
    """None when TELEGRAM_BOT_TOKEN is not configured."""
    s = get_settings()
    if s.telegram_bot_token is None:
        return None
    return TelegramClient(s.telegram_bot_token.get_secret_value(), s.telegram_api_url)
