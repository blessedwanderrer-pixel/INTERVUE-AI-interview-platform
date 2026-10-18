from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class PasswordResetRequest(BaseModel):
    email: EmailStr
    redirect_to: str | None = None


class PasswordUpdateRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UserIdentity(BaseModel):
    id: UUID
    email: EmailStr
    name: str = "Candidate"


class AuthResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    user: dict[str, Any] | None = None
    message: str | None = None


class CvRecord(BaseModel):
    id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    extraction_status: str
    extraction_error: str | None = None
    created_at: datetime


class InterviewCreate(BaseModel):
    role: str = Field(min_length=1, max_length=160)
    seniority: Literal["Early career", "Mid-level", "Senior", "Lead"]
    focus: Literal["behavioral", "technical", "leadership"]
    duration_questions: Literal[4, 6, 8]
    cv_id: UUID | None = None
    mode: Literal["text", "voice"] = "text"


class AnswerCreate(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)


class TranscriptTurnCreate(BaseModel):
    client_event_id: str = Field(min_length=8, max_length=120)
    speaker: Literal["interviewer", "candidate"]
    text: str = Field(min_length=1, max_length=12000)
    occurred_at: datetime | None = None


class VoiceTicketResponse(BaseModel):
    ticket: str
    expires_in: int
    websocket_path: str


class EvidenceReference(BaseModel):
    turn_id: UUID
    excerpt: str = Field(min_length=1, max_length=500)


class CoachingDimension(BaseModel):
    score: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1, max_length=1200)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class QuestionFeedback(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    feedback: str = Field(min_length=1, max_length=1200)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class InterviewEvaluation(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    coaching_summary: str = Field(min_length=1, max_length=2000)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    improvement_opportunities: list[str] = Field(default_factory=list, max_length=8)
    practice_topics: list[str] = Field(default_factory=list, max_length=8)
    dimensions: dict[str, CoachingDimension]
    question_feedback: list[QuestionFeedback] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list, max_length=8)


# Backward-compatible name for the existing Gemini service and tests.
GeminiEvaluation = InterviewEvaluation


class InterviewSummary(BaseModel):
    id: UUID
    role: str
    seniority: str
    focus: str
    duration_questions: int
    status: str
    current_question_index: int
    created_at: datetime
    completed_at: datetime | None = None


