"""Live Supabase verification for Phase 3 persistence and RLS. Never prints credentials."""

import json
import time
from typing import Any

import httpx
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.main import app


def require(response, status: int, label: str) -> Any:
    if response.status_code != status:
        try:
            detail = response.json()
        except ValueError:
            detail = {"message": "Non-JSON response"}
        raise AssertionError(f"{label}: expected {status}, received {response.status_code}: {detail}")
    return None if status == 204 else response.json()


def main() -> None:
    env = dotenv_values(".env")
    names = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "LIVE_TEST_USER_A_EMAIL", "LIVE_TEST_USER_A_PASSWORD", "LIVE_TEST_USER_B_EMAIL", "LIVE_TEST_USER_B_PASSWORD"]
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise RuntimeError(f"Missing live-test configuration names: {', '.join(missing)}")

    passed: list[str] = []
    session_id = None
    token_a = None
    api_key = str(env["SUPABASE_ANON_KEY"])
    base = str(env["SUPABASE_URL"])

    with TestClient(app) as client:
        try:
            login_a = require(client.post("/api/auth/login", json={"email": env["LIVE_TEST_USER_A_EMAIL"], "password": env["LIVE_TEST_USER_A_PASSWORD"]}), 200, "login A")
            login_b = require(client.post("/api/auth/login", json={"email": env["LIVE_TEST_USER_B_EMAIL"], "password": env["LIVE_TEST_USER_B_PASSWORD"]}), 200, "login B")
            token_a, token_b = login_a["access_token"], login_b["access_token"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            headers_b = {"Authorization": f"Bearer {token_b}"}
            user_a = require(client.get("/api/me", headers=headers_a), 200, "me A")
            user_b = require(client.get("/api/me", headers=headers_b), 200, "me B")
            passed.append("two_account_auth")

            session = require(client.post("/api/interviews", headers=headers_a, json={"role": "Phase 3 Voice Engineer", "seniority": "Senior", "focus": "technical", "duration_questions": 4, "mode": "voice"}), 201, "create voice interview")
            session_id = session["id"]
            assert session["mode"] == "voice" and session["questions"] == []
            passed.append("voice_session_persistence")

            direct_a = {"apikey": api_key, "Authorization": f"Bearer {token_a}", "Content-Type": "application/json", "Prefer": "return=representation"}
            direct_b = {"apikey": api_key, "Authorization": f"Bearer {token_b}", "Content-Type": "application/json", "Prefer": "return=representation"}
            turns = [
                {"session_id": session_id, "user_id": user_a["id"], "client_event_id": "live-model-0001", "speaker": "interviewer", "content": "Describe a reliability problem you solved.", "position": 0},
                {"session_id": session_id, "user_id": user_a["id"], "client_event_id": "live-candidate-0001", "speaker": "candidate", "content": "I traced timeouts, added bounded retries, measured the result, and reduced failures by thirty percent.", "position": 1},
            ]
            with httpx.Client(timeout=30) as direct:
                inserted = direct.post(f"{base}/rest/v1/interview_transcript_turns", headers=direct_a, json=turns)
                assert inserted.status_code == 201 and len(inserted.json()) == 2
                duplicate_headers = {**direct_a, "Prefer": "resolution=ignore-duplicates,return=representation"}
                duplicate = direct.post(f"{base}/rest/v1/interview_transcript_turns?on_conflict=session_id,client_event_id", headers=duplicate_headers, json=turns[1])
                assert duplicate.status_code in {200, 201} and duplicate.json() == []
                foreign_read = direct.get(f"{base}/rest/v1/interview_transcript_turns?session_id=eq.{session_id}&select=id", headers=direct_b)
                assert foreign_read.status_code == 200 and foreign_read.json() == []
                foreign_insert = direct.post(f"{base}/rest/v1/interview_transcript_turns", headers=direct_b, json={"session_id": session_id, "user_id": user_b["id"], "client_event_id": "foreign-event-0001", "speaker": "candidate", "content": "Forbidden", "position": 2})
                assert foreign_insert.status_code in {401, 403}
            passed.extend(["transcript_persistence", "duplicate_prevention", "transcript_rls_isolation"])

            transcript = require(client.get(f"/api/voice/sessions/{session_id}/transcript", headers=headers_a), 200, "owner transcript")
            assert len(transcript) == 2
            assert client.get(f"/api/voice/sessions/{session_id}/transcript", headers=headers_b).status_code == 404
            passed.append("transcript_api_ownership")

            evaluation_started = time.monotonic()
            completed = require(client.post(f"/api/voice/sessions/{session_id}/complete", headers=headers_a), 200, "complete voice interview")
            evaluation_duration_seconds = round(time.monotonic() - evaluation_started, 3)
            expected_type = "groq" if env.get("GROQ_API_KEY") and not completed["warning"] else "heuristic"
            assert completed["evaluation"]["evaluation_type"] == expected_type
            if expected_type == "groq":
                assert completed["evaluation"]["model_name"] == env.get("GROQ_EVALUATION_MODEL", "openai/gpt-oss-20b")
            report = require(client.get(f"/api/interviews/{session_id}/report", headers=headers_a), 200, "voice report")
            assert len(report["transcript"]) == 2 and report["evaluation"]["evaluation_type"] == expected_type
            assert client.get(f"/api/interviews/{session_id}/report", headers=headers_b).status_code == 404
            assert any(item["evaluation_type"] == "heuristic" for item in report["evaluations"])
            passed.extend([
                "groq_evaluation_persistence" if expected_type == "groq" else "heuristic_fallback",
                "voice_report_retrieval",
                "report_rls_isolation",
            ])

            with httpx.Client(timeout=30) as direct:
                cleanup_headers = {**direct_a, "Prefer": "return=minimal"}
                cleanup = direct.delete(f"{base}/rest/v1/interview_sessions?id=eq.{session_id}", headers=cleanup_headers)
                assert cleanup.status_code == 204
            session_id = None
            passed.append("test_data_cleanup")
        finally:
            if token_a and session_id:
                cleanup_headers = {"apikey": api_key, "Authorization": f"Bearer {token_a}", "Content-Type": "application/json", "Prefer": "return=minimal"}
                with httpx.Client(timeout=30) as direct:
                    direct.delete(f"{base}/rest/v1/interview_sessions?id=eq.{session_id}", headers=cleanup_headers)

    print(json.dumps({"live_phase_3_persistence": {name: "passed" for name in passed}, "passed": len(passed), "groq_evaluation": "live" if "groq_evaluation_persistence" in passed else "fallback", "evaluation_duration_seconds": evaluation_duration_seconds}, indent=2))


if __name__ == "__main__":
    main()

