from __future__ import annotations

import json
import base64
import asyncio
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
import websockets

from app.config import Settings
from app.errors import AppError
from app.models import GeminiEvaluation

logger = logging.getLogger(__name__)
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def build_interviewer_instruction(session: dict[str, Any], cv_text: str | None, transcript: list[dict[str, Any]]) -> str:
    context = cv_text.strip()[:16000] if cv_text else "CV text is unavailable. Do not imply that you reviewed it."
    prior = "\n".join(f"{turn['speaker']}: {turn['content']}" for turn in transcript[-24:]) or "No prior turns."
    return f"""You are INTERVUE, a professional practice interviewer. This is coaching, never a hiring decision.
Target role: {session['role']}
Seniority: {session['seniority']}
Focus: {session['focus']}
Primary-question target: {session['duration_questions']}
Candidate CV context (use only facts written here):
{context}

Prior saved transcript for recovery:
{prior}

Introduce yourself briefly, explain that microphone audio is processed for this live session and raw audio is not retained, then ask one primary question at a time. Cover introduction, role-relevant knowledge, projects or experience when evidenced, problem-solving, behavior, and scenarios. Listen fully, ask concise relevant follow-ups, avoid repetition, never invent experience, and say when evidence is unavailable. After the primary-question target, tell the candidate the interview is ready to finish. Do not infer protected traits, emotions, or employability from voice."""


class GeminiService:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.last_diagnostics: dict[str, Any] = {}

    def _require_key(self) -> None:
        if not self.settings.gemini_configured:
            raise AppError(503, "gemini_not_configured", "Gemini is not configured on the server. Add GEMINI_API_KEY to backend/.env.")

    @asynccontextmanager
    async def live(self, instruction: str, resume_handle: str | None = None) -> AsyncIterator[Any]:
        self._require_key()
        session = RawGeminiLiveSession(self.settings, instruction, resume_handle)
        try:
            await session.connect()
            yield session
        except AppError:
            raise
        except Exception as exc:
            raise AppError(502, "gemini_live_failed", "Gemini Live could not start or the connection ended unexpectedly.") from exc
        finally:
            await session.close()

    async def evaluate(self, *, session: dict[str, Any], transcript: list[dict[str, Any]]) -> GeminiEvaluation:
        self._require_key()
        started = time.monotonic()
        transcript_payload = [{"turn_id": row["id"], "speaker": row["speaker"], "text": row["content"]} for row in transcript]
        prompt = f"""Evaluate this practice interview for coaching only. Base every claim on the supplied transcript. Evidence excerpts must be exact substrings from the referenced turn. Use empty evidence and list the issue under missing_evidence when the transcript does not demonstrate a dimension. Do not infer protected traits, emotion, personality, employability, or facts outside the transcript.
Role: {session['role']} | Seniority: {session['seniority']} | Focus: {session['focus']}
Transcript JSON:
{json.dumps(transcript_payload, ensure_ascii=False)}"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self.settings.gemini_evaluation_model)}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": _supported_json_schema(GeminiEvaluation.model_json_schema()), "maxOutputTokens": 4096, "thinkingConfig": {"thinkingLevel": "low"}},
        }
        build_seconds = time.monotonic() - started
        attempts: list[dict[str, Any]] = []
        last_error: Exception | None = None
        max_attempts = max(1, self.settings.gemini_evaluation_max_attempts)
        budget = max(1.0, self.settings.gemini_evaluation_budget_seconds)
        async with httpx.AsyncClient(transport=self.transport) as client:
            for attempt in range(1, max_attempts + 1):
                remaining = budget - (time.monotonic() - started)
                if remaining <= 0:
                    break
                request_started = time.monotonic()
                try:
                    response = await client.post(
                        url,
                        headers={"x-goog-api-key": self.settings.gemini_api_key},
                        json=payload,
                        timeout=min(self.settings.gemini_evaluation_attempt_timeout_seconds, remaining),
                    )
                    attempts.append({"attempt": attempt, "duration_seconds": round(time.monotonic() - request_started, 3), "status": response.status_code})
                    if response.status_code != 200:
                        error = httpx.HTTPStatusError("Gemini provider error", request=response.request, response=response)
                        if response.status_code not in _TRANSIENT_STATUS:
                            raise AppError(502, "gemini_provider_rejected", "Gemini rejected the evaluation request.") from error
                        raise error
                    data = response.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    evaluation = GeminiEvaluation.model_validate_json(text)
                    validate_evidence(evaluation, transcript)
                    self.last_diagnostics = self._diagnostics(started, build_seconds, prompt, transcript, attempts)
                    logger.info("Gemini evaluation completed", extra={"gemini_diagnostics": self.last_diagnostics})
                    return evaluation
                except AppError:
                    raise
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                    last_error = exc
                    if not attempts or attempts[-1].get("attempt") != attempt:
                        attempts.append({"attempt": attempt, "duration_seconds": round(time.monotonic() - request_started, 3), "error_type": type(exc).__name__})
                    else:
                        attempts[-1]["error_type"] = type(exc).__name__
                    if attempt >= max_attempts:
                        break
                    backoff = min(2 ** (attempt - 1) + random.uniform(0, 0.25), max(0.0, budget - (time.monotonic() - started)))
                    if backoff <= 0:
                        break
                    await asyncio.sleep(backoff)
                except Exception as exc:
                    last_error = exc
                    attempts.append({"attempt": attempt, "duration_seconds": round(time.monotonic() - request_started, 3), "error_type": type(exc).__name__})
                    break
        self.last_diagnostics = self._diagnostics(started, build_seconds, prompt, transcript, attempts)
        logger.warning("Gemini evaluation failed", extra={"gemini_diagnostics": self.last_diagnostics, "error_type": type(last_error).__name__ if last_error else "BudgetExceeded"})
        timed_out = isinstance(last_error, httpx.TimeoutException)
        raise AppError(502, "gemini_evaluation_timeout" if timed_out else "gemini_evaluation_failed", "Gemini evaluation exceeded its safety time budget." if timed_out else "Gemini could not generate a valid interview evaluation.") from last_error

    @staticmethod
    def _diagnostics(started: float, build_seconds: float, prompt: str, transcript: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "build_seconds": round(build_seconds, 3),
            "total_seconds": round(time.monotonic() - started, 3),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "transcript_turns": len(transcript),
            "attempts": attempts,
        }


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _supported_json_schema(schema: Any) -> Any:
    """Keep only the JSON Schema subset documented by Gemini structured output."""
    if isinstance(schema, list):
        return [_supported_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    allowed = {"$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum", "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf", "properties", "additionalProperties", "required", "propertyOrdering"}
    output: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key in {"properties", "$defs"} and isinstance(value, dict):
            output[key] = {name: _supported_json_schema(item) for name, item in value.items()}
        else:
            output[key] = _supported_json_schema(value)
    return output


def _objectify(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{_snake(key): _objectify(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_objectify(item) for item in value]
    return value


class RawGeminiLiveSession:
    """Small adapter around Google's documented v1beta Live WebSocket protocol."""

    endpoint = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

    def __init__(self, settings: Settings, instruction: str, resume_handle: str | None):
        self.settings = settings
        self.instruction = instruction
        self.resume_handle = resume_handle
        self.websocket: Any = None

    async def connect(self) -> None:
        url = f"{self.endpoint}?key={quote(self.settings.gemini_api_key, safe='')}"
        self.websocket = await websockets.connect(url, open_timeout=15, close_timeout=5, max_size=8 * 1024 * 1024)
        setup: dict[str, Any] = {
            "model": f"models/{self.settings.gemini_live_model}",
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "systemInstruction": {"parts": [{"text": self.instruction}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "contextWindowCompression": {"slidingWindow": {}},
            "sessionResumption": {"handle": self.resume_handle} if self.resume_handle else {},
        }
        await self.websocket.send(json.dumps({"setup": setup}))
        response = json.loads(await asyncio_wait(self.websocket.recv(), 15))
        if "setupComplete" not in response:
            raise AppError(502, "gemini_setup_failed", "Gemini Live did not confirm the session setup.")

    async def close(self) -> None:
        if self.websocket is not None:
            try:
                await self.websocket.close()
            except Exception:
                pass

    async def send_client_content(self, *, turns: Any, turn_complete: bool = True) -> None:
        payload_turns = turns if isinstance(turns, list) else [turns]
        await self.websocket.send(json.dumps({"clientContent": {"turns": payload_turns, "turnComplete": turn_complete}}))

    async def send_realtime_input(self, *, audio: bytes | None = None, mime_type: str = "audio/pcm;rate=16000", audio_stream_end: bool | None = None) -> None:
        realtime: dict[str, Any] = {}
        if audio is not None:
            realtime["audio"] = {"data": base64.b64encode(audio).decode("ascii"), "mimeType": mime_type}
        if audio_stream_end:
            realtime["audioStreamEnd"] = True
        await self.websocket.send(json.dumps({"realtimeInput": realtime}))

    async def receive(self) -> AsyncIterator[Any]:
        async for raw in self.websocket:
            payload = json.loads(raw)
            parts = payload.get("serverContent", {}).get("modelTurn", {}).get("parts", [])
            for part in parts:
                inline = part.get("inlineData")
                if inline and isinstance(inline.get("data"), str):
                    inline["data"] = base64.b64decode(inline["data"])
            yield _objectify(payload)


async def asyncio_wait(awaitable: Any, seconds: float) -> Any:
    import asyncio
    async with asyncio.timeout(seconds):
        return await awaitable


def validate_evidence(evaluation: GeminiEvaluation, transcript: list[dict[str, Any]]) -> None:
    turns = {str(row["id"]): row["content"] for row in transcript}
    references = [ref for dimension in evaluation.dimensions.values() for ref in dimension.evidence]
    references.extend(ref for item in evaluation.question_feedback for ref in item.evidence)
    for reference in references:
        source = turns.get(str(reference.turn_id))
        if not source or reference.excerpt.casefold() not in source.casefold():
            raise AppError(502, "ungrounded_evaluation", "Gemini returned evaluation evidence that was not present in the transcript.")

