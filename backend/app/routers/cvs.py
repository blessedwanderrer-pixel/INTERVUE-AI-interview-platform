from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, UploadFile

from app.config import Settings, get_settings
from app.dependencies import get_access_token, get_current_user, get_gateway
from app.errors import AppError
from app.models import UserIdentity
from app.services.cv import validate_and_extract
from app.services.supabase import SupabaseGateway

router = APIRouter(prefix="/cvs", tags=["cvs"])


@router.get("")
async def list_cvs(
    token: Annotated[str, Depends(get_access_token)],
    _: Annotated[UserIdentity, Depends(get_current_user)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
) -> list[dict]:
    return await gateway.rest("GET", "cv_documents", token, query="?select=id,original_name,mime_type,size_bytes,extraction_status,extraction_error,created_at&order=created_at.desc")


@router.post("", status_code=201)
async def upload_cv(
    token: Annotated[str, Depends(get_access_token)],
    user: Annotated[UserIdentity, Depends(get_current_user)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(...),
) -> dict:
    content = await file.read(settings.max_cv_bytes + 1)
    extracted = validate_and_extract(file.filename or "cv", content, settings.max_cv_bytes)
    object_path = f"{user.id}/{uuid4()}{extracted.extension}"
    await gateway.upload(token, object_path, content, extracted.mime_type)
    try:
        rows = await gateway.rest(
            "POST", "cv_documents", token,
            json={"user_id": str(user.id), "original_name": file.filename, "storage_path": object_path, "mime_type": extracted.mime_type, "size_bytes": len(content), "extracted_text": extracted.text or None, "extraction_status": extracted.status, "extraction_error": extracted.error},
            prefer="return=representation",
        )
    except Exception:
        await gateway.remove_storage(token, [object_path])
        raise
    return rows[0]


@router.delete("/{cv_id}", status_code=204)
async def delete_cv(
    cv_id: str,
    token: Annotated[str, Depends(get_access_token)],
    _: Annotated[UserIdentity, Depends(get_current_user)],
    gateway: Annotated[SupabaseGateway, Depends(get_gateway)],
) -> None:
    rows = await gateway.rest("GET", "cv_documents", token, query=f"?id=eq.{cv_id}&select=id,storage_path")
    if not rows:
        raise AppError(404, "cv_not_found", "CV not found.")
    await gateway.remove_storage(token, [rows[0]["storage_path"]])
    await gateway.rest("DELETE", "cv_documents", token, query=f"?id=eq.{cv_id}")


