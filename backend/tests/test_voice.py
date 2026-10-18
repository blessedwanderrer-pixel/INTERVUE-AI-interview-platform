import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import httpx
import groq as groq_sdk
from unittest.mock import AsyncMock

from app.config import Settings, get_settings
from app.errors import AppError
from app.main import app
from app.models import GeminiEvaluation
from app.services.gemini import GeminiService, _supported_json_schema, validate_evidence
from app.services.groq import GroqEvaluationService
from app.services.voice_tickets import VoiceTicketStore


VOICE_BODY = {"role": "Backend Engineer", "seniority": "Mid-level", "focus": "technical", "duration_questions": 4, "mode": "voice"}


def gemini_response(turn_id):
    evaluation = {
        "overall_score": 81,
        "coaching_summary": "Clear, evidence-based explanation.",
        "strengths": ["Quantified impact"],
        "improvement_opportunities": [],
        "practice_topics": ["Trade-off discussion"],
        "missing_evidence": [],
        "dimensions": {"technical_accuracy": {"score": 82, "rationale": "Supported by the answer.", "evidence": [{"turn_id": str(turn_id), "excerpt": "reduced it by thirty percent"}]}},
        "question_feedback": [],
    }
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(evaluation)}]}}]}


def evaluation_input():
    turn_id = uuid4()
    session = {"role": "Backend Engineer", "seniority": "Mid-level", "focus": "technical"}
    transcript = [{"id": str(turn_id), "speaker": "candidate", "content": "I measured latency and reduced it by thirty percent."}]
    return turn_id, session, transcript


class FakeGroqClient:
    def __init__(self, side_effect):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=side_effect)))
        self.close = AsyncMock()


def groq_sdk_response(turn_id):
    content = gemini_response(turn_id)["candidates"][0]["content"]["parts"][0]["text"]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_unauthorized_voice_session_rejected(client):
    response = client.post("/api/voice/sessions/00000000-0000-4000-8000-000000000001")
    assert response.status_code == 401


def test_voice_session_ownership_enforced(client, auth_headers, gateway):
    gateway.sessions.append({"id": "foreign", "user_id": "22222222-2222-4222-8222-222222222222", **VOICE_BODY, "status": "in_progress", "current_question_index": 0})
    app.dependency_overrides[get_settings] = lambda: Settings(gemini_api_key="test-only")
    response = client.post("/api/voice/sessions/foreign", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_gemini_session_creation_failure_without_key():
    with pytest.raises(AppError) as caught:
        async with GeminiService(Settings(gemini_api_key="")).live("instruction"):
            pass
    assert caught.value.code == "gemini_not_configured"


@pytest.mark.asyncio
async def test_audio_session_ticket_lifecycle_and_limit():
    store = VoiceTicketStore()
    token = await store.issue(user_id="user", access_token="secret", session_id="session", ttl_seconds=60)
    ticket = await store.consume(token)
    assert ticket.session_id == "session"
    with pytest.raises(AppError):
        await store.consume(token)
    await store.acquire("user", 1)
    with pytest.raises(AppError) as caught:
        await store.acquire("user", 1)
    assert caught.value.status_code == 429
    await store.release("user")
    await store.acquire("user", 1)


def test_microphone_permission_and_recovery_paths_exist():
    source = (Path(__file__).parents[2] / "app.js").read_text(encoding="utf-8")
    assert "NotAllowedError" in source
    assert "getUserMedia" in source
    assert "RECONNECTING" in source
    assert "echoCancellation:true" in source


def test_microphone_streaming_cleanup_and_denial_paths_exist():
    source = (Path(__file__).parents[2] / "app.js").read_text(encoding="utf-8")
    assert "voice.socket.send(pcm.buffer)" in source
    assert "voice.stream?.getTracks().forEach(track=>track.stop())" in source
    assert "voice.processor?.disconnect()" in source
    assert "voice.audioContext?.close()" in source
    assert "Microphone permission was denied" in source
    backend_source = (Path(__file__).parents[1] / "app" / "routers" / "voice.py").read_text(encoding="utf-8")
    assert '"raw_audio_retained": False' in backend_source


def test_vercel_voice_session_is_bounded_and_auto_completes():
    settings = Settings()
    assert settings.voice_max_session_seconds < 300
    source = (Path(__file__).parents[2] / "app.js").read_text(encoding="utf-8")
    assert "max_session_seconds" in source
    assert "completeVoiceInterview('The five-minute interview limit was reached.')" in source
    assert "clearTimeout(voice.durationTimeout)" in source


@pytest.mark.asyncio
async def test_duplicate_transcript_event_is_ignored(gateway):
    payload = {"session_id": "session", "user_id": "11111111-1111-4111-8111-111111111111", "client_event_id": "event-0001", "speaker": "candidate", "content": "A saved answer", "position": 0}
    first = await gateway.rest("POST", "interview_transcript_turns", "good-token", query="?on_conflict=session_id,client_event_id", json=payload)
    second = await gateway.rest("POST", "interview_transcript_turns", "good-token", query="?on_conflict=session_id,client_event_id", json=payload)
    assert len(first) == 1
    assert second == []
    assert len(gateway.transcript) == 1


def test_evaluation_schema_and_grounding():
    turn_id = uuid4()
    transcript = [{"id": str(turn_id), "content": "I measured latency and reduced it by thirty percent."}]
    evaluation = GeminiEvaluation.model_validate({
        "overall_score": 81,
        "coaching_summary": "Clear, evidence-based explanation.",
        "strengths": ["Quantified impact"],
        "improvement_opportunities": [],
        "practice_topics": ["Trade-off discussion"],
        "missing_evidence": [],
        "dimensions": {"technical_accuracy": {"score": 82, "rationale": "Supported by the answer.", "evidence": [{"turn_id": str(turn_id), "excerpt": "reduced it by thirty percent"}]}},
        "question_feedback": [],
    })
    validate_evidence(evaluation, transcript)
    evaluation.dimensions["technical_accuracy"].evidence[0].excerpt = "fabricated quote"
    with pytest.raises(AppError) as caught:
        validate_evidence(evaluation, transcript)
    assert caught.value.code == "ungrounded_evaluation"


def test_gemini_schema_uses_supported_subset():
    schema = _supported_json_schema(GeminiEvaluation.model_json_schema())
    encoded = str(schema)
    assert "minLength" not in encoded and "maxLength" not in encoded and "default" not in encoded
    assert "$defs" in schema and "properties" in schema


@pytest.mark.asyncio
async def test_gemini_structured_evaluation_success():
    turn_id, session, transcript = evaluation_input()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request, json=gemini_response(turn_id)))
    service = GeminiService(Settings(gemini_api_key="test-only"), transport=transport)
    result = await service.evaluate(session=session, transcript=transcript)
    assert result.overall_score == 81
    assert service.last_diagnostics["attempts"][0]["status"] == 200


@pytest.mark.asyncio
async def test_gemini_timeout_is_retried_then_bounded(monkeypatch):
    _, session, transcript = evaluation_input()
    calls = 0
    def timeout(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)
    monkeypatch.setattr("app.services.gemini.asyncio.sleep", AsyncMock())
    service = GeminiService(Settings(gemini_api_key="test-only", gemini_evaluation_max_attempts=2), transport=httpx.MockTransport(timeout))
    with pytest.raises(AppError) as caught:
        await service.evaluate(session=session, transcript=transcript)
    assert caught.value.code == "gemini_evaluation_timeout" and calls == 2


@pytest.mark.asyncio
async def test_gemini_transient_failure_then_success(monkeypatch):
    turn_id, session, transcript = evaluation_input()
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, request=request, json={} if calls == 1 else gemini_response(turn_id))
    monkeypatch.setattr("app.services.gemini.asyncio.sleep", AsyncMock())
    result = await GeminiService(Settings(gemini_api_key="test-only"), transport=httpx.MockTransport(handler)).evaluate(session=session, transcript=transcript)
    assert result.overall_score == 81 and calls == 2


@pytest.mark.asyncio
async def test_gemini_permanent_error_is_not_retried():
    _, session, transcript = evaluation_input()
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, request=request, json={"error": {"message": "invalid"}})
    with pytest.raises(AppError) as caught:
        await GeminiService(Settings(gemini_api_key="test-only"), transport=httpx.MockTransport(handler)).evaluate(session=session, transcript=transcript)
    assert caught.value.code == "gemini_provider_rejected" and calls == 1


@pytest.mark.asyncio
async def test_invalid_gemini_response_is_not_accepted():
    _, session, transcript = evaluation_input()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request, json={"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]}))
    with pytest.raises(AppError) as caught:
        await GeminiService(Settings(gemini_api_key="test-only"), transport=transport).evaluate(session=session, transcript=transcript)
    assert caught.value.code == "gemini_evaluation_failed"


@pytest.mark.asyncio
async def test_groq_structured_evaluation_success():
    turn_id, session, transcript = evaluation_input()
    client = FakeGroqClient([groq_sdk_response(turn_id)])
    service = GroqEvaluationService(Settings(groq_api_key="test-only"), client_factory=lambda **_: client)
    result = await service.evaluate(session=session, transcript=transcript)
    assert result.overall_score == 81
    assert service.last_diagnostics["attempts"][0]["status"] == "success"
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_groq_invalid_api_key_is_not_retried():
    _, session, transcript = evaluation_input()
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(401, request=request)
    client = FakeGroqClient([groq_sdk.AuthenticationError("invalid", response=response, body={})])
    with pytest.raises(AppError) as caught:
        await GroqEvaluationService(Settings(groq_api_key="invalid"), client_factory=lambda **_: client).evaluate(session=session, transcript=transcript)
    assert caught.value.code == "groq_provider_rejected"
    assert client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_groq_timeout_is_bounded_and_retried(monkeypatch):
    _, session, transcript = evaluation_input()
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    client = FakeGroqClient([groq_sdk.APITimeoutError(request=request), groq_sdk.APITimeoutError(request=request)])
    monkeypatch.setattr("app.services.groq.asyncio.sleep", AsyncMock())
    with pytest.raises(AppError) as caught:
        await GroqEvaluationService(Settings(groq_api_key="test-only"), client_factory=lambda **_: client).evaluate(session=session, transcript=transcript)
    assert caught.value.code == "groq_evaluation_timeout"
    assert client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_groq_invalid_structured_json_is_rejected():
    _, session, transcript = evaluation_input()
    client = FakeGroqClient([SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))])])
    with pytest.raises(AppError) as caught:
        await GroqEvaluationService(Settings(groq_api_key="test-only"), client_factory=lambda **_: client).evaluate(session=session, transcript=transcript)
    assert caught.value.code == "groq_evaluation_failed"


@pytest.mark.asyncio
async def test_groq_rate_limit_then_success(monkeypatch):
    turn_id, session, transcript = evaluation_input()
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)
    client = FakeGroqClient([groq_sdk.RateLimitError("limited", response=response, body={}), groq_sdk_response(turn_id)])
    monkeypatch.setattr("app.services.groq.asyncio.sleep", AsyncMock())
    result = await GroqEvaluationService(Settings(groq_api_key="test-only"), client_factory=lambda **_: client).evaluate(session=session, transcript=transcript)
    assert result.overall_score == 81
    assert client.chat.completions.create.await_count == 2


def test_voice_completion_preserves_heuristic_fallback(client, auth_headers, gateway):
    app.dependency_overrides[get_settings] = lambda: Settings(groq_api_key="", gemini_api_key="", gemini_evaluation_fallback_enabled=False)
    created = client.post("/api/interviews", headers=auth_headers, json=VOICE_BODY).json()
    gateway.transcript.extend([
        {"id": str(uuid4()), "session_id": created["id"], "user_id": created["user_id"], "client_event_id": "model-0001", "speaker": "interviewer", "content": "Tell me about a system you improved.", "position": 0},
        {"id": str(uuid4()), "session_id": created["id"], "user_id": created["user_id"], "client_event_id": "candidate-0001", "speaker": "candidate", "content": "I measured latency, changed the cache strategy, and reduced p95 latency by thirty percent.", "position": 1},
    ])
    response = client.post(f"/api/voice/sessions/{created['id']}/complete", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["evaluation"]["evaluation_type"] == "heuristic"
    assert body["warning"]
    assert gateway.sessions[0]["status"] == "completed"


def test_voice_completion_reuses_persisted_gemini_evaluation(client, auth_headers, gateway, monkeypatch):
    turn_id, _, _ = evaluation_input()
    app.dependency_overrides[get_settings] = lambda: Settings(groq_api_key="test-only")
    created = client.post("/api/interviews", headers=auth_headers, json=VOICE_BODY).json()
    gateway.transcript.append({"id": str(turn_id), "session_id": created["id"], "user_id": created["user_id"], "client_event_id": "candidate-0001", "speaker": "candidate", "content": "I measured latency and reduced it by thirty percent.", "position": 0})
    generated = GeminiEvaluation.model_validate(json.loads(gemini_response(turn_id)["candidates"][0]["content"]["parts"][0]["text"]))
    evaluate = AsyncMock(return_value=generated)
    monkeypatch.setattr("app.routers.voice.GroqEvaluationService.evaluate", evaluate)
    first = client.post(f"/api/voice/sessions/{created['id']}/complete", headers=auth_headers)
    second = client.post(f"/api/voice/sessions/{created['id']}/complete", headers=auth_headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["evaluation"]["evaluation_type"] == second.json()["evaluation"]["evaluation_type"] == "groq"
    assert evaluate.await_count == 1
    assert len([row for row in gateway.evaluations if row["evaluation_type"] == "groq"]) == 1


def test_text_interview_remains_default(client, auth_headers):
    body = {key: value for key, value in VOICE_BODY.items() if key != "mode"}
    response = client.post("/api/interviews", headers=auth_headers, json=body)
    assert response.status_code == 201
    assert response.json()["mode"] == "text"
    assert len(response.json()["questions"]) == 4

