"""Real Gemini provider smoke test. Reads backend/.env and never prints the key or content."""

import asyncio
import json
import sys
from uuid import uuid4

from app.config import Settings
from app.errors import AppError
from app.services.gemini import GeminiService


async def main() -> None:
    settings = Settings()
    if not settings.gemini_configured:
        raise RuntimeError("GEMINI_API_KEY is not configured in backend/.env")
    service = GeminiService(settings)
    results = {"live_setup": False, "live_audio_received": False, "live_transcription_received": False, "evaluation_schema_and_grounding": False}
    try:
        if "--evaluation-only" not in sys.argv:
          async with service.live("You are testing a voice interview connection. Reply with one very short spoken greeting.") as session:
            results["live_setup"] = True
            await session.send_client_content(turns={"role": "user", "parts": [{"text": "Say: Welcome to the interview."}]}, turn_complete=True)
            async with asyncio.timeout(45):
                async for message in session.receive():
                    content = getattr(message, "server_content", None)
                    if content:
                        if getattr(getattr(content, "output_transcription", None), "text", None):
                            results["live_transcription_received"] = True
                        model_turn = getattr(content, "model_turn", None)
                        for part in getattr(model_turn, "parts", []) if model_turn else []:
                            if getattr(getattr(part, "inline_data", None), "data", None):
                                results["live_audio_received"] = True
                        if getattr(content, "turn_complete", False):
                            break

        turn_id = uuid4()
        transcript = [
            {"id": str(turn_id), "speaker": "candidate", "content": "I measured the retry failure rate, bounded retries, and reduced failures by thirty percent."}
        ]
        evaluation = await service.evaluate(
            session={"role": "Backend Engineer", "seniority": "Senior", "focus": "technical"},
            transcript=transcript,
        )
        results["evaluation_schema_and_grounding"] = evaluation.overall_score >= 0
    except AppError as exc:
        cause = exc.__cause__
        hint = str(cause) if isinstance(cause, ValueError) and str(cause).startswith("Provider status") else ""
        print(json.dumps({"real_gemini": results, "error_code": exc.code, "error": exc.message, "cause_type": type(cause).__name__, "safe_hint": hint}, indent=2))
        raise SystemExit(1)
    print(json.dumps({"real_gemini": results, "models": {"live": settings.gemini_live_model, "evaluation": settings.gemini_evaluation_model}}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

