from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.errors import AppError


class SupabaseGateway:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _ensure_configured(self) -> None:
        if not self.settings.configured:
            raise AppError(503, "supabase_not_configured", "Supabase credentials are not configured on the server.")

    def _headers(self, access_token: str | None = None, content_type: str = "application/json") -> dict[str, str]:
        self._ensure_configured()
        token = access_token or self.settings.supabase_anon_key
        return {"apikey": self.settings.supabase_anon_key, "Authorization": f"Bearer {token}", "Content-Type": content_type}

    async def _request(self, method: str, url: str, *, headers: dict[str, str], expected: set[int], **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
        if response.status_code not in expected:
            try:
                payload = response.json()
                message = payload.get("msg") or payload.get("message") or payload.get("error_description") or "Supabase request failed."
            except ValueError:
                message = "Supabase request failed."
            status = 401 if response.status_code in {400, 401, 403} else 502
            raise AppError(status, "supabase_request_failed", message)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def register(self, name: str, email: str, password: str) -> dict[str, Any]:
        return await self._request("POST", f"{self.settings.supabase_url}/auth/v1/signup", headers=self._headers(), expected={200}, json={"email": email, "password": password, "data": {"name": name}})

    async def login(self, email: str, password: str) -> dict[str, Any]:
        return await self._request("POST", f"{self.settings.supabase_url}/auth/v1/token?grant_type=password", headers=self._headers(), expected={200}, json={"email": email, "password": password})

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._request("POST", f"{self.settings.supabase_url}/auth/v1/token?grant_type=refresh_token", headers=self._headers(), expected={200}, json={"refresh_token": refresh_token})

    async def logout(self, access_token: str) -> None:
        await self._request("POST", f"{self.settings.supabase_url}/auth/v1/logout", headers=self._headers(access_token), expected={200, 204})

    async def reset_password(self, email: str, redirect_to: str | None) -> None:
        payload: dict[str, str] = {"email": email}
        if redirect_to:
            payload["redirect_to"] = redirect_to
        await self._request("POST", f"{self.settings.supabase_url}/auth/v1/recover", headers=self._headers(), expected={200}, json=payload)

    async def update_password(self, access_token: str, password: str) -> dict[str, Any]:
        return await self._request("PUT", f"{self.settings.supabase_url}/auth/v1/user", headers=self._headers(access_token), expected={200}, json={"password": password})

    async def get_user(self, access_token: str) -> dict[str, Any]:
        return await self._request("GET", f"{self.settings.supabase_url}/auth/v1/user", headers=self._headers(access_token), expected={200})

    async def rest(self, method: str, table: str, access_token: str, *, query: str = "", json: Any = None, prefer: str | None = None) -> Any:
        headers = self._headers(access_token)
        if prefer:
            headers["Prefer"] = prefer
        return await self._request(method, f"{self.settings.supabase_url}/rest/v1/{table}{query}", headers=headers, expected={200, 201, 204}, json=json)

    async def upload(self, access_token: str, path: str, content: bytes, mime_type: str) -> None:
        headers = self._headers(access_token, mime_type)
        headers["x-upsert"] = "false"
        await self._request("POST", f"{self.settings.supabase_url}/storage/v1/object/{self.settings.cv_bucket}/{quote(path)}", headers=headers, expected={200}, content=content)

    async def remove_storage(self, access_token: str, paths: list[str]) -> None:
        await self._request("DELETE", f"{self.settings.supabase_url}/storage/v1/object/{self.settings.cv_bucket}", headers=self._headers(access_token), expected={200}, json={"prefixes": paths})


