from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Callable

import groq
from groq import AsyncGroq

from app.config import Settings
from app.errors import AppError
from app.models import InterviewEvaluation
from app.services.gemini import validate_evidence

logger = logging.getLogger(__name__)


class GroqEvaluationService:
    def __init__(self, settings: Settings, *, client_factory: Callable[..., Any] = AsyncGroq):
        self.settings = settings
        self.client_factory = client_factory
        self.last_diagnostics: dict[str, Any] = {}

    def _require_key(self) -> None:
        if not self.settings.groq_configured:
            raise AppError(503, "groq_not_configured", "Groq evaluation is not configured on the server.")

    async def evaluate(self, *, session: dict[str, Any], transcript: list[dict[str, Any]]) -> InterviewEvaluation:
        self._require_key()
        started = time.monotonic()
        transcript_payload = [{"turn_id": row["id"], "speaker": row["speaker"], "text": row["content"]} for row in transcript]
        prompt = (
            "Evaluate this practice interview for coaching only. Base every claim only on the supplied saved transcript. "
            "Evidence excerpts must be exact substrings from the referenced turn. Use empty evidence and list the issue "
            "under missing_evidence when a dimension is not demonstrated. Do not infer protected traits, emotion, "
            "personality, employability, or facts outside the transcript.\n"
            f"Role: {session['role']} | Seniority: {session['seniority']} | Focus: {session['focus']}\n"
            f"Transcript JSON:\n{json.dumps(transcript_payload, ensure_ascii=False)}"
        )
        schema = InterviewEvaluation.model_json_schema()
        attempts: list[dict[str, Any]] = []
        last_error: Exception | None = None
        max_attempts = max(1, self.settings.groq_evaluation_max_attempts)
        budget = max(1.0, self.settings.groq_evaluation_budget_seconds)
        client = self.client_factory(api_key=self.settings.groq_api_key, timeout=self.settings.groq_evaluation_attempt_timeout_seconds, max_retries=0)
        try:
            for attempt in range(1, max_attempts + 1):
                remaining = budget - (time.monotonic() - started)
                if remaining <= 0:
                    break
                request_started = time.monotonic()
                try:
                    response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=self.settings.groq_evaluation_model,
                            messages=[
                                {"role": "system", "content": "Return only the requested structured interview evaluation."},
                                {"role": "user", "content": prompt},
                            ],
                            response_format={"type": "json_schema", "json_schema": {"name": "interview_evaluation", "strict": False, "schema": schema}},
                            temperature=0,
                        ),
                        timeout=min(self.settings.groq_evaluation_attempt_timeout_seconds, remaining),
                    )
                    content = response.choices[0].message.content
                    evaluation = InterviewEvaluation.model_validate_json(content or "")
                    validate_evidence(evaluation, transcript)
                    attempts.append({"attempt": attempt, "duration_seconds": round(time.monotonic() - request_started, 3), "status": "success"})
                    self.last_diagnostics = self._diagnostics(started, prompt, transcript, attempts)
                    logger.info("Groq evaluation completed", extra={"groq_diagnostics": self.last_diagnostics})
                    return evaluation
                except (asyncio.TimeoutError, groq.APITimeoutError, groq.APIConnectionError, groq.RateLimitError, groq.InternalServerError) as exc:
                    last_error = exc
                    attempts.append({"attempt": attempt, "duration_seconds": round(time.monotonic() - request_started, 3), "error_type": type(exc).__name__})
                    if attempt >= max_attempts:
                        break
                    delay = min(2 ** (attempt - 1) + random.uniform(0, 0.25), max(0.0, budget - (time.monotonic() - started)))
                    if delay <= 0:
                        break
                    await asyncio.sleep(delay)
                except (groq.AuthenticationError, groq.PermissionDeniedError, groq.BadRequestError) as exc:
                    raise AppError(502, "groq_provider_rejected", "Groq rejected the evaluation request.") from exc
                except AppError:
                    raise
                except Exception as exc:
                    last_error = exc
                    attempts.append({"attempt": attempt, "duration_seconds": round(time.monotonic() - request_started, 3), "error_type": type(exc).__name__})
                    break
        finally:
            await client.close()
        self.last_diagnostics = self._diagnostics(started, prompt, transcript, attempts)
        logger.warning("Groq evaluation failed", extra={"groq_diagnostics": self.last_diagnostics, "error_type": type(last_error).__name__ if last_error else "BudgetExceeded"})
        timed_out = isinstance(last_error, (asyncio.TimeoutError, groq.APITimeoutError))
        raise AppError(502, "groq_evaluation_timeout" if timed_out else "groq_evaluation_failed", "Groq evaluation exceeded its safety time budget." if timed_out else "Groq could not generate a valid interview evaluation.") from last_error

    @staticmethod
    def _diagnostics(started: float, prompt: str, transcript: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "total_seconds": round(time.monotonic() - started, 3),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "transcript_turns": len(transcript),
            "attempts": attempts,
        }

