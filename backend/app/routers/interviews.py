from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_access_token, get_current_user, get_gateway
from app.config import Settings, get_settings
from app.errors import AppError
from app.models import AnswerCreate, InterviewCreate, UserIdentity
from app.services.evaluation import score_answers
from app.services.gemini import GeminiService
from app.services.groq import GroqEvaluationService
from app.services.supabase import SupabaseGateway

router = APIRouter(prefix="/interviews", tags=["interviews"])

QUESTIONS = {
    "behavioral": ["Walk me through the experience that best prepared you for this role.", "Tell me about a difficult problem you owned. What made it difficult?", "Describe a time you received feedback that changed your approach.", "Tell me about a decision you made with incomplete information.", "Describe a disagreement with a teammate and how you handled it.", "What outcome are you most proud of, and what was specifically yours?", "Tell me about a setback. What did you do next?", "Why is this role the right next step for you?"],
    "technical": ["Choose a project that represents your strongest craft. What trade-offs did you make?", "How do you turn an ambiguous requirement into a workable plan?", "Describe a technical decision you would make differently today.", "How do you validate the quality of your work before it ships?", "Tell me about a performance, reliability, or usability problem you diagnosed.", "How do you communicate technical constraints to non-technical partners?", "What is your approach to learning an unfamiliar system quickly?", "How would you improve the first 30 days in this role?"],
    "leadership": ["Tell me about a team outcome you influenced without formal authority.", "How have you raised the quality bar for others?", "Describe a conflict between speed and quality that you had to resolve.", "Tell me about someone you helped grow.", "How do you make priorities clear when everything feels urgent?", "Describe a decision that was unpopular but necessary.", "What operating principle do you bring to a team?", "What would your first 90 days in this role look like?"],
}


async def load_session(gateway: SupabaseGateway, token: str, session_id: str) -> dict:
    rows = await gateway.rest("GET", "interview_sessions", token, query=f"?id=eq.{session_id}&select=*")
    if not rows:
        raise AppError(404, "interview_not_found", "Interview session not found.")
    return rows[0]


@router.get("")
async def history(token: Annotated[str, Depends(get_access_token)], _: Annotated[UserIdentity, Depends(get_current_user)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> list[dict]:
    return await gateway.rest("GET", "interview_sessions", token, query="?select=id,role,seniority,focus,duration_questions,status,current_question_index,created_at,completed_at&order=created_at.desc")


@router.post("", status_code=201)
async def create_interview(body: InterviewCreate, token: Annotated[str, Depends(get_access_token)], user: Annotated[UserIdentity, Depends(get_current_user)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> dict:
    sessions = await gateway.rest("POST", "interview_sessions", token, json={"user_id": str(user.id), **body.model_dump(mode="json")}, prefer="return=representation")
    session = sessions[0]
    if body.mode == "text":
        question_rows = [{"session_id": session["id"], "user_id": str(user.id), "position": index, "question_text": question} for index, question in enumerate(QUESTIONS[body.focus][:body.duration_questions])]
        await gateway.rest("POST", "interview_questions", token, json=question_rows, prefer="return=representation")
    return await get_interview(session["id"], token, user, gateway)


@router.get("/{session_id}")
async def get_interview(session_id: str, token: Annotated[str, Depends(get_access_token)], _: Annotated[UserIdentity, Depends(get_current_user)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> dict:
    session = await load_session(gateway, token, session_id)
    session["questions"] = await gateway.rest("GET", "interview_questions", token, query=f"?session_id=eq.{session_id}&select=id,position,question_text&order=position")
    session["answers"] = await gateway.rest("GET", "candidate_answers", token, query=f"?session_id=eq.{session_id}&select=id,question_id,answer_text,word_count,created_at&order=created_at")
    return session


@router.post("/{session_id}/answers", status_code=201)
async def save_answer(session_id: str, body: AnswerCreate, token: Annotated[str, Depends(get_access_token)], user: Annotated[UserIdentity, Depends(get_current_user)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> dict:
    session = await load_session(gateway, token, session_id)
    if session["status"] != "in_progress":
        raise AppError(409, "interview_complete", "This interview is already complete.")
    questions = await gateway.rest("GET", "interview_questions", token, query=f"?session_id=eq.{session_id}&position=eq.{session['current_question_index']}&select=id,question_text")
    if not questions:
        raise AppError(409, "question_not_found", "The next interview question is unavailable.")
    answers = await gateway.rest("POST", "candidate_answers", token, json={"session_id": session_id, "question_id": questions[0]["id"], "user_id": str(user.id), "answer_text": body.answer.strip(), "word_count": len(body.answer.split())}, prefer="return=representation")
    next_index = session["current_question_index"] + 1
    await gateway.rest("PATCH", "interview_sessions", token, query=f"?id=eq.{session_id}", json={"current_question_index": next_index, "updated_at": datetime.now(timezone.utc).isoformat()}, prefer="return=minimal")
    return answers[0]


@router.post("/{session_id}/complete")
async def complete_interview(session_id: str, token: Annotated[str, Depends(get_access_token)], user: Annotated[UserIdentity, Depends(get_current_user)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)], settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    session = await load_session(gateway, token, session_id)
    answers = await gateway.rest("GET", "candidate_answers", token, query=f"?session_id=eq.{session_id}&select=id,question_id,answer_text,word_count,created_at&order=created_at")
    if len(answers) < session["duration_questions"]:
        raise AppError(409, "interview_incomplete", "Answer every question before completing the interview.")
    evaluation = score_answers(answers)
    rows = await gateway.rest("POST", "interview_evaluations", token, query="?on_conflict=session_id,evaluation_type", json={"session_id": session_id, "user_id": str(user.id), **evaluation}, prefer="resolution=ignore-duplicates,return=representation")
    if not rows:
        rows = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.heuristic&select=*")
    questions = await gateway.rest("GET", "interview_questions", token, query=f"?session_id=eq.{session_id}&select=id,question_text,position&order=position")
    question_by_id = {row["id"]: row for row in questions}
    transcript = []
    for answer in answers:
        question = question_by_id.get(answer["question_id"])
        if question:
            transcript.append({"id": question["id"], "speaker": "interviewer", "content": question["question_text"]})
        transcript.append({"id": answer["id"], "speaker": "candidate", "content": answer["answer_text"]})
    ai_row = None
    warning = None
    try:
        existing = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.groq&select=*")
        if existing:
            ai_row = existing[0]
        else:
            result = await GroqEvaluationService(settings).evaluate(session=session, transcript=transcript)
            persisted = await gateway.rest("POST", "interview_evaluations", token, query="?on_conflict=session_id,evaluation_type", json={"session_id": session_id, "user_id": str(user.id), "evaluation_type": "groq", "rubric_version": "groq-coaching-v1", "notice": "AI-generated coaching estimate grounded in saved interview answers; not a hiring decision.", "overall_score": result.overall_score, "scores": {key: value.score for key, value in result.dimensions.items()}, "details": result.model_dump(mode="json"), "model_name": settings.groq_evaluation_model}, prefer="resolution=ignore-duplicates,return=representation")
            ai_row = persisted[0] if persisted else (await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.groq&select=*"))[0]
    except AppError as exc:
        warning = exc.message
    if ai_row is None and settings.gemini_evaluation_fallback_enabled:
        try:
            existing = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&evaluation_type=eq.gemini&select=*")
            if existing:
                ai_row = existing[0]
            else:
                result = await GeminiService(settings).evaluate(session=session, transcript=transcript)
                persisted = await gateway.rest("POST", "interview_evaluations", token, query="?on_conflict=session_id,evaluation_type", json={"session_id": session_id, "user_id": str(user.id), "evaluation_type": "gemini", "rubric_version": "gemini-coaching-v1", "notice": "AI-generated coaching estimate grounded in saved interview answers; not a hiring decision.", "overall_score": result.overall_score, "scores": {key: value.score for key, value in result.dimensions.items()}, "details": result.model_dump(mode="json"), "model_name": settings.gemini_evaluation_model}, prefer="resolution=ignore-duplicates,return=representation")
                ai_row = persisted[0] if persisted else None
        except AppError as exc:
            warning = " ".join(item for item in (warning, exc.message) if item)
    now = datetime.now(timezone.utc).isoformat()
    await gateway.rest("PATCH", "interview_sessions", token, query=f"?id=eq.{session_id}", json={"status": "completed", "completed_at": now, "current_question_index": session["duration_questions"], "updated_at": now}, prefer="return=minimal")
    return {"evaluation": ai_row or rows[0], "evaluations": [item for item in (ai_row, rows[0]) if item], "answers": answers, "session": session, "warning": warning}


@router.get("/{session_id}/report")
async def get_report(session_id: str, token: Annotated[str, Depends(get_access_token)], _: Annotated[UserIdentity, Depends(get_current_user)], gateway: Annotated[SupabaseGateway, Depends(get_gateway)]) -> dict:
    session = await load_session(gateway, token, session_id)
    evaluations = await gateway.rest("GET", "interview_evaluations", token, query=f"?session_id=eq.{session_id}&select=*&order=created_at.desc")
    if not evaluations:
        raise AppError(404, "report_not_found", "This interview does not have a completed report.")
    answers = await gateway.rest("GET", "candidate_answers", token, query=f"?session_id=eq.{session_id}&select=id,question_id,answer_text,word_count,created_at&order=created_at")
    transcript = await gateway.rest("GET", "interview_transcript_turns", token, query=f"?session_id=eq.{session_id}&select=id,speaker,content,position,occurred_at,created_at&order=position")
    preferred = next((item for source in ("groq", "gemini", "heuristic") for item in evaluations if item["evaluation_type"] == source), evaluations[0])
    return {"product": "INTERVUE", "session": session, "evaluation": preferred, "evaluations": evaluations, "answers": answers, "transcript": transcript}


