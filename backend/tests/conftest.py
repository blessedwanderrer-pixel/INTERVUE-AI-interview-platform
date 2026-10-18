from datetime import datetime, timezone
import re
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_gateway
from app.config import Settings, get_settings
from app.errors import AppError
from app.main import app

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime.now(timezone.utc).isoformat()


class FakeGateway:
    def __init__(self):
        self.sessions, self.questions, self.answers, self.evaluations, self.cvs, self.transcript = [], [], [], [], [], []
        self.uploads = {}

    async def register(self, name, email, password):
        return {"access_token": "good-token", "refresh_token": "refresh", "expires_in": 3600, "user": {"id": str(USER_ID), "email": email, "user_metadata": {"name": name}}}

    async def login(self, email, password):
        if password == "wrong-password":
            raise AppError(401, "supabase_request_failed", "Invalid login credentials")
        return await self.register("Candidate", email, password)

    async def refresh(self, refresh_token): return await self.register("Candidate", "candidate@example.com", "password")
    async def logout(self, access_token): return None
    async def reset_password(self, email, redirect_to): return None
    async def update_password(self, access_token, password): return {"id": str(USER_ID)}

    async def get_user(self, access_token):
        if access_token != "good-token": raise AppError(401, "supabase_request_failed", "Invalid token")
        return {"id": str(USER_ID), "email": "candidate@example.com", "user_metadata": {"name": "Candidate"}}

    async def upload(self, access_token, path, content, mime_type): self.uploads[path] = content
    async def remove_storage(self, access_token, paths):
        for path in paths: self.uploads.pop(path, None)

    async def rest(self, method, table, access_token, query="", json=None, prefer=None):
        store = {"cv_documents": self.cvs, "interview_sessions": self.sessions, "interview_questions": self.questions, "candidate_answers": self.answers, "interview_evaluations": self.evaluations, "interview_transcript_turns": self.transcript}[table]
        def matches(row):
            for field in ("id", "session_id", "position", "evaluation_type"):
                match = re.search(rf"(?:[?&]){field}=eq\.([^&]+)", query)
                if match and str(row.get(field)) != match.group(1): return False
            return row.get("user_id", str(USER_ID)) == str(USER_ID)
        if method == "GET": return [dict(row) for row in store if matches(row)]
        if method == "POST":
            items, created = (json if isinstance(json, list) else [json]), []
            for item in items:
                if "on_conflict=session_id,client_event_id" in query:
                    duplicate = next((row for row in store if row.get("session_id") == item.get("session_id") and row.get("client_event_id") == item.get("client_event_id")), None)
                    if duplicate:
                        continue
                if "on_conflict=session_id,evaluation_type" in query:
                    duplicate = next((row for row in store if row.get("session_id") == item.get("session_id") and row.get("evaluation_type") == item.get("evaluation_type")), None)
                    if duplicate:
                        duplicate.update(item); created.append(dict(duplicate)); continue
                row = {"id": str(uuid4()), "created_at": NOW, **item}
                if table == "interview_sessions": row.update({"status": "in_progress", "current_question_index": 0, "completed_at": None, "updated_at": NOW})
                store.append(row); created.append(dict(row))
            return created
        if method == "PATCH":
            for row in store:
                if matches(row): row.update(json)
            return []
        if method == "DELETE":
            store[:] = [row for row in store if not matches(row)]
            return None
        raise AssertionError(method)


@pytest.fixture
def gateway(): return FakeGateway()


@pytest.fixture
def client(gateway):
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[get_settings] = lambda: Settings(groq_api_key="", gemini_api_key="", gemini_evaluation_fallback_enabled=False)
    with TestClient(app) as test_client: yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(): return {"Authorization": "Bearer good-token"}


