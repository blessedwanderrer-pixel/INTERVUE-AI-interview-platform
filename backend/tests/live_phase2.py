"""Manual live Phase 2 verification. Reads credentials from backend/.env and never prints them."""

import json
from typing import Any

import httpx
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.main import app


def require(response, status: int, label: str) -> dict[str, Any] | list[Any] | None:
    if response.status_code != status:
        try:
            detail = response.json()
        except ValueError:
            detail = {"message": "Non-JSON response"}
        raise AssertionError(f"{label}: expected {status}, received {response.status_code}: {detail}")
    if status == 204:
        return None
    return response.json()


def main() -> None:
    env = dotenv_values(".env")
    required = [
        "SUPABASE_URL", "SUPABASE_ANON_KEY",
        "LIVE_TEST_USER_A_EMAIL", "LIVE_TEST_USER_A_PASSWORD",
        "LIVE_TEST_USER_B_EMAIL", "LIVE_TEST_USER_B_PASSWORD",
    ]
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise RuntimeError(f"Missing live-test configuration names: {', '.join(missing)}")

    results: dict[str, str] = {}
    created_cv_id: str | None = None
    created_session_id: str | None = None
    token_a: str | None = None
    api_key = str(env["SUPABASE_ANON_KEY"])
    supabase_url = str(env["SUPABASE_URL"])

    def passed(name: str) -> None:
        results[name] = "passed"

    with TestClient(app) as client:
        try:
            health = require(client.get("/api/health"), 200, "health")
            assert health["supabase_configured"] is True
            passed("backend_start_and_health")

            assert client.get("/api/me", headers={"Authorization": "Bearer deliberately-invalid"}).status_code == 401
            passed("invalid_token_rejected")

            login_a = require(client.post("/api/auth/login", json={"email": env["LIVE_TEST_USER_A_EMAIL"], "password": env["LIVE_TEST_USER_A_PASSWORD"]}), 200, "login A")
            login_b = require(client.post("/api/auth/login", json={"email": env["LIVE_TEST_USER_B_EMAIL"], "password": env["LIVE_TEST_USER_B_PASSWORD"]}), 200, "login B")
            token_a, refresh_a = login_a["access_token"], login_a["refresh_token"]
            token_b, refresh_b = login_b["access_token"], login_b["refresh_token"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            headers_b = {"Authorization": f"Bearer {token_b}"}
            passed("real_login_two_accounts")

            me_a = require(client.get("/api/me", headers=headers_a), 200, "me A")
            me_b = require(client.get("/api/me", headers=headers_b), 200, "me B")
            assert me_a["id"] != me_b["id"]
            passed("token_verification")

            cv = require(client.post("/api/cvs", headers=headers_a, files={"file": ("phase2-live.txt", b"Experienced candidate who delivered measurable outcomes and learned from feedback.", "text/plain")}), 201, "CV upload")
            created_cv_id = cv["id"]
            assert cv["extraction_status"] == "complete"
            passed("cv_upload_and_text_extraction")

            cvs_a = require(client.get("/api/cvs", headers=headers_a), 200, "list CV A")
            cvs_b = require(client.get("/api/cvs", headers=headers_b), 200, "list CV B")
            assert any(item["id"] == created_cv_id for item in cvs_a)
            assert all(item["id"] != created_cv_id for item in cvs_b)
            assert client.delete(f"/api/cvs/{created_cv_id}", headers=headers_b).status_code == 404
            passed("cv_rls_isolation")

            interview = require(client.post("/api/interviews", headers=headers_a, json={"role": "Phase 2 Integration Engineer", "seniority": "Senior", "focus": "technical", "duration_questions": 4, "cv_id": created_cv_id}), 201, "create interview")
            created_session_id = interview["id"]
            assert len(interview["questions"]) == 4
            passed("interview_creation")

            assert client.get(f"/api/interviews/{created_session_id}", headers=headers_b).status_code == 404
            direct_b = {"apikey": api_key, "Authorization": f"Bearer {token_b}", "Content-Type": "application/json"}
            with httpx.Client(timeout=30) as direct:
                foreign_read = direct.get(f"{supabase_url}/rest/v1/interview_sessions?id=eq.{created_session_id}&select=id", headers=direct_b)
                assert foreign_read.status_code == 200 and foreign_read.json() == []
                foreign_insert = direct.post(f"{supabase_url}/rest/v1/interview_sessions", headers=direct_b, json={"user_id": me_a["id"], "role": "Forbidden", "seniority": "Senior", "focus": "technical", "duration_questions": 4})
                assert foreign_insert.status_code in {401, 403}
            passed("two_account_rls_read_write_isolation")

            require(client.post(f"/api/interviews/{created_session_id}/answers", headers=headers_a, json={"answer": "I owned the design, measured the result, and changed the implementation after feedback."}), 201, "save first answer")
            resumed = require(client.get(f"/api/interviews/{created_session_id}", headers=headers_a), 200, "resume interview")
            assert resumed["current_question_index"] == 1 and len(resumed["answers"]) == 1
            passed("answer_persistence_and_resumption")

            history_a = require(client.get("/api/interviews", headers=headers_a), 200, "history A")
            history_b = require(client.get("/api/interviews", headers=headers_b), 200, "history B")
            assert any(item["id"] == created_session_id for item in history_a)
            assert all(item["id"] != created_session_id for item in history_b)
            passed("history_isolation")

            for index in range(1, 4):
                require(client.post(f"/api/interviews/{created_session_id}/answers", headers=headers_a, json={"answer": f"I owned decision {index}, delivered an improved result, and learned from feedback."}), 201, f"save answer {index + 1}")
            completed = require(client.post(f"/api/interviews/{created_session_id}/complete", headers=headers_a), 200, "complete interview")
            assert completed["evaluation_type"] == "heuristic"
            report = require(client.get(f"/api/interviews/{created_session_id}/report", headers=headers_a), 200, "report retrieval")
            assert report["evaluation"]["evaluation_type"] == "heuristic" and len(report["answers"]) == 4
            assert client.get(f"/api/interviews/{created_session_id}/report", headers=headers_b).status_code == 404
            passed("completion_and_report_retrieval")

            require(client.delete(f"/api/cvs/{created_cv_id}", headers=headers_a), 204, "CV deletion")
            created_cv_id = None
            assert all(item["original_name"] != "phase2-live.txt" for item in require(client.get("/api/cvs", headers=headers_a), 200, "CV deletion check"))
            passed("cv_deletion")

            direct_a = {"apikey": api_key, "Authorization": f"Bearer {token_a}", "Content-Type": "application/json", "Prefer": "return=minimal"}
            with httpx.Client(timeout=30) as direct:
                cleanup = direct.delete(f"{supabase_url}/rest/v1/interview_sessions?id=eq.{created_session_id}", headers=direct_a)
                assert cleanup.status_code == 204
            created_session_id = None
            passed("test_data_cleanup")

            require(client.post("/api/auth/logout", headers=headers_a), 204, "logout A")
            require(client.post("/api/auth/logout", headers=headers_b), 204, "logout B")
            assert client.post("/api/auth/refresh", json={"refresh_token": refresh_a}).status_code == 401
            assert client.post("/api/auth/refresh", json={"refresh_token": refresh_b}).status_code == 401
            passed("logout_and_refresh_revocation")
        finally:
            if token_a:
                cleanup_headers = {"apikey": api_key, "Authorization": f"Bearer {token_a}", "Content-Type": "application/json", "Prefer": "return=minimal"}
                with httpx.Client(timeout=30) as direct:
                    if created_session_id:
                        direct.delete(f"{supabase_url}/rest/v1/interview_sessions?id=eq.{created_session_id}", headers=cleanup_headers)
                if created_cv_id:
                    client.delete(f"/api/cvs/{created_cv_id}", headers={"Authorization": f"Bearer {token_a}"})

    print(json.dumps({"live_phase_2": results, "passed": len(results)}, indent=2))


if __name__ == "__main__":
    main()

