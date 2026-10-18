from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.dependencies import get_current_user
from app.models import UserIdentity

router = APIRouter(tags=["core"])


@router.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str | bool]:
    return {"status": "ok", "service": settings.app_name, "supabase_configured": settings.configured, "gemini_configured": settings.gemini_configured, "groq_configured": settings.groq_configured}


@router.get("/me", response_model=UserIdentity)
async def me(user: Annotated[UserIdentity, Depends(get_current_user)]) -> UserIdentity:
    return user


