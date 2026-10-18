from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.config import Settings, get_settings
from app.dependencies import get_access_token, get_current_user, get_gateway
from app.errors import AppError
from app.models import UserIdentity, VoiceTicketResponse
from app.routers.interviews import load_session
from app.services.evaluation import score_answers
from app.services.gemini import GeminiService, build_interviewer_instruction
from app.services.groq import GroqEvaluationService
from app.services.supabase import SupabaseGateway
from app.services.voice_tickets import voice_tickets

router = APIRouter(prefix="/voice", tags=["voice"])


async def _transcript(gateway: SupabaseGateway, token: str, session_id: str) -> list[dict[str, Any]]:
    return await gateway.rest(
        "GET", "interview_transcript_turns", token,
        query=f"?session_id=eq.{session_id}&select=id,client_event_id,speaker,content,position,occurred_at,created_at&order=position",
    )


async def _save_turn(gateway: SupabaseGateway, token: str, ticket: Any, speaker: str, content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    turns = await _transcript(gateway, token, ticket.session_id)
    event_id = f"gemini-{speaker}-{uuid4()}"
    rows = await gateway.rest(
        "POST", "interview_transcript_turns", token,
        query="?on_conflict=session_id,client_event_id",
        json={
            "session_id": ticket.session_id,
            "user_id": ticket.user_id,
            "client_event_id": event_id,
            "speaker": speaker,
            "content": text,
            "position": len(turns),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        prefer="resolution=ignore-duplicates,return=representation",
    )
    return rows[0] if rows else None


@router.post("/sessions/{session_id}", response_model=VoiceTicketResponse)
async def create_voice_ticket(
    session_id: str,
    token: Annotated[str, Depends(get_access_token)],
    user: Annotated[UserIdentity, Depends(get_current_user)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceTicketResponse:
    if not settings.gemini_configured:
        raise AppError(503, "gemini_not_configured", "Gemini is not configured on the server. Add GEMINI_API_KEY to backend/.env.")
    session = await load_session(gateway, token, session_id)
    if session["status"] == "completed":
        raise AppError(409, "interview_complete", "This interview is already complete.")
    if session.get("mode") != "voice":
        raise AppError(409, "not_voice_interview", "This interview was not created in voice mode.")
    ticket = await voice_tickets.issue(user_id=str(user.id), access_token=token, session_id=session_id, ttl_seconds=settings.voice_ticket_ttl_seconds)
    await gateway.rest("PATCH", "interview_sessions", token, query=f"?id=eq.{session_id}", json={"status": "in_progress", "updated_at": datetime.now(timezone.utc).isoformat()}, prefer="return=minimal")
    return VoiceTicketResponse(ticket=ticket, expires_in=settings.voice_ticket_ttl_seconds, websocket_path=f"/api/voice/ws?ticket={ticket}")


@router.get("/sessions/{session_id}/transcript")
async def get_transcript(
    session_id: str,
    token: Annotated[str, Depends(get_access_token)],
    _: Annotated[UserIdentity, Depends(get_current_user)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
) -> list[dict[str, Any]]:
    await load_session(gateway, token, session_id)
    return await _transcript(gateway, token, session_id)


@router.websocket("/ws")
async def voice_websocket(websocket: WebSocket, ticket: str = "") -> None:
    settings = get_settings()
    gateway = SupabaseGateway(settings)
    acquired_user: str | None = None
    try:
        voice_ticket = await voice_tickets.consume(ticket)
        await voice_tickets.acquire(voice_ticket.user_id, settings.voice_max_active_per_user)
        acquired_user = voice_ticket.user_id
        user = await gateway.get_user(voice_ticket.access_token)
        if user.get("id") != voice_ticket.user_id:
            raise AppError(401, "invalid_voice_ticket", "Voice ticket identity did not match the authenticated user.")
        session = await load_session(gateway, voice_ticket.access_token, voice_ticket.session_id)
        if session.get("mode") != "voice" or session.get("status") == "completed":
            raise AppError(409, "voice_session_unavailable", "This voice interview cannot be opened.")
        cv_text = None
        if session.get("cv_id"):
            cvs = await gateway.rest("GET", "cv_documents", voice_ticket.access_token, query=f"?id=eq.{session['cv_id']}&select=extracted_text,extraction_status")
            if cvs and cvs[0].get("extraction_status") == "complete":
                cv_text = cvs[0].get("extracted_text")
        transcript = await _transcript(gateway, voice_ticket.access_token, voice_ticket.session_id)
        instruction = build_interviewer_instruction(session, cv_text, transcript)
        await websocket.accept()
        service = GeminiService(settings)
        async with service.live(instruction) as live:
            await websocket.send_json({"type": "connected", "input_rate": 16000, "output_rate": 24000, "raw_audio_retained": False})
            await live.send_client_content(turns={"role": "user", "parts": [{"text": "Begin the interview now with the introduction and first question."}]}, turn_complete=True)

            async def client_to_gemini() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("bytes") is not None:
                        chunk = message["bytes"]
                        if not chunk or len(chunk) > settings.voice_max_chunk_bytes or len(chunk) % 2:
                            await websocket.send_json({"type": "error", "code": "invalid_audio_chunk", "message": "Audio chunks must be bounded 16-bit PCM."})
                            continue
                        await live.send_realtime_input(audio=chunk, mime_type="audio/pcm;rate=16000")
                    elif message.get("text"):
                        payload = __import__("json").loads(message["text"])
                        if payload.get("type") == "end":
                            await live.send_realtime_input(audio_stream_end=True)
                            return

            async def gemini_to_client() -> None:
                candidate_buffer: list[str] = []
                interviewer_buffer: list[str] = []
                async for response in live.receive():
                    content = getattr(response, "server_content", None)
                    if content:
                        input_text = getattr(getattr(content, "input_transcription", None), "text", None)
                        output_text = getattr(getattr(content, "output_transcription", None), "text", None)
                        if input_text:
                            candidate_buffer.append(input_text)
                            await websocket.send_json({"type": "transcript_delta", "speaker": "candidate", "text": input_text})
                        if output_text:
                            interviewer_buffer.append(output_text)
                            await websocket.send_json({"type": "transcript_delta", "speaker": "interviewer", "text": output_text})
                        model_turn = getattr(content, "model_turn", None)
                        for part in getattr(model_turn, "parts", []) if model_turn else []:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                await websocket.send_bytes(inline.data)
                        if getattr(content, "interrupted", False):
                            await websocket.send_json({"type": "interrupted"})
                        if getattr(content, "turn_complete", False):
                            saved = []
                            if candidate_buffer:
                                row = await _save_turn(gateway, voice_ticket.access_token, voice_ticket, "candidate", "".join(candidate_buffer))
                                if row: saved.append(row)
                            if interviewer_buffer:
                                row = await _save_turn(gateway, voice_ticket.access_token, voice_ticket, "interviewer", "".join(interviewer_buffer))
                                if row: saved.append(row)
                            candidate_buffer.clear(); interviewer_buffer.clear()
                            await websocket.send_json({"type": "turn_complete", "saved": saved})
                    update = getattr(response, "session_resumption_update", None)
                    if update and getattr(update, "resumable", False) and getattr(update, "new_handle", None):
                        await websocket.send_json({"type": "resume_available"})
                    go_away = getattr(response, "go_away", None)
                    if go_away:
                        await websocket.send_json({"type": "reconnect_required"})

            async with asyncio.timeout(settings.voice_max_session_minutes * 60):
                tasks = [asyncio.create_task(client_to_gemini()), asyncio.create_task(gemini_to_client())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending: task.cancel()
                for task in done: task.result()
    except WebSocketDisconnect:
        pass
    except TimeoutError:
        try: await websocket.send_json({"type": "ended", "reason": "duration_limit"})
        except Exception: pass
    except AppError as exc:
        try:
            await websocket.close(code=4400 + min(exc.status_code, 99), reason=exc.message[:120])
        except Exception:
            pass
    except Exception:
        try: await websocket.close(code=1011, reason="Voice session failed safely.")
        except Exception: pass
    finally:
        if acquired_user:
            await voice_tickets.release(acquired_user)


@router.post("/sessions/{session_id}/complete")
async def complete_voice_interview(
    session_id: str,
    token: Annotated[str, Depends(get_access_token)],
    user: Annotated[UserIdentity, Depends(get_current_user)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    session = await load_session(gateway, token, session_id)
    if session.get("mode") != "voice":
        raise AppError(409, "not_voice_interview", "This interview was not created in voice mode.")
    transcript = await _transcript(gateway, token, session_id)
    candidate_turns = [row for row in transcript if row["speaker"] == "candidate"]
    if not candidate_turns:
        raise AppError(409, "interview_incomplete", "At least one transcribed candidate response is required.")
    heuristic = score_answers([{"answer_text": row["content"], "word_count": len(row["content"].split())} for row in candidate_turns])
    heuristic_rows = await gateway.rest(
        "POST", "interview_evaluations", token, query="?on_conflict=session_id,evaluation_type",
        json={"session_id": session_id, "user_id": str(user.id), **heuristic},
        prefer="resolution=ignore-duplicates,return=representation",
    )
    if not heuristic_rows:
        heuristic_rows = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.heuristic&select=*")
    groq_row = None
    gemini_row = None
    warnings: list[str] = []
    try:
        existing = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.groq&select=*")
        if existing:
            groq_row = existing[0]
        else:
            evaluation = await GroqEvaluationService(settings).evaluate(session=session, transcript=transcript)
            details = evaluation.model_dump(mode="json")
            scores = {key: value.score for key, value in evaluation.dimensions.items()}
            rows = await gateway.rest(
                "POST", "interview_evaluations", token, query="?on_conflict=session_id,evaluation_type",
                json={"session_id": session_id, "user_id": str(user.id), "evaluation_type": "groq", "rubric_version": "groq-coaching-v1", "notice": "AI-generated coaching estimate grounded in the saved transcript; not a hiring decision.", "overall_score": evaluation.overall_score, "scores": scores, "details": details, "model_name": settings.groq_evaluation_model},
                prefer="resolution=ignore-duplicates,return=representation",
            )
            if rows:
                groq_row = rows[0]
            else:
                persisted = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.groq&select=*")
                groq_row = persisted[0]
    except AppError as exc:
        warnings.append(exc.message)
    if groq_row is None and settings.gemini_evaluation_fallback_enabled:
        try:
            existing = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.gemini&select=*")
            if existing:
                gemini_row = existing[0]
            else:
                evaluation = await GeminiService(settings).evaluate(session=session, transcript=transcript)
                details = evaluation.model_dump(mode="json")
                scores = {key: value.score for key, value in evaluation.dimensions.items()}
                rows = await gateway.rest(
                    "POST", "interview_evaluations", token, query="?on_conflict=session_id,evaluation_type",
                    json={"session_id": session_id, "user_id": str(user.id), "evaluation_type": "gemini", "rubric_version": "gemini-coaching-v1", "notice": "AI-generated coaching estimate grounded in the saved transcript; not a hiring decision.", "overall_score": evaluation.overall_score, "scores": scores, "details": details, "model_name": settings.gemini_evaluation_model},
                    prefer="resolution=ignore-duplicates,return=representation",
                )
                gemini_row = rows[0] if rows else (await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.gemini&select=*"))[0]
        except AppError as exc:
            warnings.append(exc.message)
    now = datetime.now(timezone.utc).isoformat()
    await gateway.rest("PATCH", "interview_sessions", token, query=f"?id=eq.{session_id}&status=neq.completed", json={"status": "completed", "completed_at": now, "updated_at": now}, prefer="return=minimal")
    return {"session": {**session, "status": "completed", "completed_at": now}, "evaluation": groq_row or gemini_row or heuristic_rows[0], "evaluations": [row for row in [groq_row, gemini_row, heuristic_rows[0]] if row], "transcript": transcript, "warning": " ".join(warnings) or None}

