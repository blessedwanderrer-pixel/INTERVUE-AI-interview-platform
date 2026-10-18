from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.errors import AppError
from app.models import UserIdentity
from app.services.supabase import SupabaseGateway

bearer = HTTPBearer(auto_error=False)


def get_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> SupabaseGateway:
    return SupabaseGateway(settings)


async def get_access_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AppError(401, "authentication_required", "A valid bearer token is required.")
    return credentials.credentials


async def get_current_user(
    token: Annotated[str, Depends(get_access_token)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
) -> UserIdentity:
    payload = await gateway.get_user(token)
    try:
        metadata = payload.get("user_metadata") or {}
        return UserIdentity(id=UUID(payload["id"]), email=payload["email"], name=metadata.get("name") or "Candidate")
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(401, "invalid_token", "The authenticated user payload is invalid.") from exc


