from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from app.errors import AppError


@dataclass(frozen=True)
class VoiceTicket:
    user_id: str
    access_token: str
    session_id: str
    expires_at: float


class VoiceTicketStore:
    """One-use, short-lived WebSocket tickets. Use a shared store for multi-worker deploys."""

    def __init__(self) -> None:
        self._tickets: dict[str, VoiceTicket] = {}
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def issue(self, *, user_id: str, access_token: str, session_id: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        async with self._lock:
            now = time.monotonic()
            self._tickets = {key: value for key, value in self._tickets.items() if value.expires_at > now}
            self._tickets[token] = VoiceTicket(user_id, access_token, session_id, now + ttl_seconds)
        return token

    async def consume(self, token: str) -> VoiceTicket:
        async with self._lock:
            ticket = self._tickets.pop(token, None)
        if not ticket or ticket.expires_at <= time.monotonic():
            raise AppError(401, "invalid_voice_ticket", "The voice connection ticket is invalid or expired.")
        return ticket

    async def acquire(self, user_id: str, maximum: int) -> None:
        async with self._lock:
            count = self._active.get(user_id, 0)
            if count >= maximum:
                raise AppError(429, "voice_session_limit", "An active voice interview already exists for this account.")
            self._active[user_id] = count + 1

    async def release(self, user_id: str) -> None:
        async with self._lock:
            count = self._active.get(user_id, 0)
            if count <= 1:
                self._active.pop(user_id, None)
            else:
                self._active[user_id] = count - 1


voice_tickets = VoiceTicketStore()

