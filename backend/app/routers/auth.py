from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.dependencies import get_access_token, get_gateway
from app.models import AuthResponse, LoginRequest, PasswordResetRequest, PasswordUpdateRequest, RefreshRequest, RegisterRequest
from app.services.supabase import SupabaseGateway

router = APIRouter(prefix="/auth", tags=["auth"])


def to_auth_response(payload: dict) -> AuthResponse:
    return AuthResponse(
        access_token=payload.get("access_token"),
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in"),
        user=payload.get("user"),
        message="Check your email to confirm your account." if payload.get("user") and not payload.get("access_token") else None,
    )


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> AuthResponse:
    return to_auth_response(await gateway.register(body.name.strip(), body.email, body.password))


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> AuthResponse:
    return to_auth_response(await gateway.login(body.email, body.password))


@router.post("/refresh", response_model=AuthResponse)
async def refresh(body: RefreshRequest, gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> AuthResponse:
    return to_auth_response(await gateway.refresh(body.refresh_token))


@router.post("/logout", status_code=204)
async def logout(token: Annotated[str, Depends(get_access_token)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> Response:
    await gateway.logout(token)
    return Response(status_code=204)


@router.post("/password-reset", status_code=202)
async def password_reset(body: PasswordResetRequest, gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> dict[str, str]:
    await gateway.reset_password(body.email, body.redirect_to)
    return {"message": "If an account exists, password reset instructions have been sent."}


@router.post("/password-update")
async def password_update(body: PasswordUpdateRequest, token: Annotated[str, Depends(get_access_token)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> dict[str, str]:
    await gateway.update_password(token, body.password)
    return {"message": "Password updated. You can continue to your workspace."}


