from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.errors import install_error_handlers
from app.routers import auth, core, cvs, interviews, voice

settings = get_settings()
app = FastAPI(title=settings.app_name, version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
install_error_handlers(app)
app.include_router(core.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(cvs.router, prefix="/api")
app.include_router(interviews.router, prefix="/api")
app.include_router(voice.router, prefix="/api")

