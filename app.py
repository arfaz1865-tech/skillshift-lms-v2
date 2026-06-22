"""
Production-oriented FastAPI backend for an AI voice interview agent.

Implemented upgrades:
- Backend owns interview state.
- SQLite by default, PostgreSQL via DATABASE_URL.
- Questions, answers, transcripts, per-answer scores, and final reports are persisted.
- AI responses use strict JSON schemas instead of fragile free-form JSON parsing.
- WebSocket answer streaming stores audio chunks while the candidate is speaking.
- Client-side silence detection can close an answer after a pause; backend then transcribes,
  evaluates, advances the session, and returns the next question.

Environment variables:
- OPENAI_API_KEY: required for real OpenAI calls.
- DATABASE_URL: optional. Defaults to sqlite:///./interview_agent.db.
  PostgreSQL example: postgresql+psycopg2://user:password@localhost:5432/interview_agent
- OPENAI_CHAT_MODEL: optional. Defaults to gpt-4o-mini.
- OPENAI_TRANSCRIBE_MODEL: optional. Defaults to gpt-4o-mini-transcribe.
- OPENAI_REALTIME_MODEL: optional. Defaults to gpt-realtime-2.
- UPLOAD_DIR: optional. Defaults to ./uploads.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Literal, Optional

import jwt
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

try:
    import httpx
    from openai import AsyncOpenAI
    from supabase import create_client
except ImportError:  # pragma: no cover
    httpx = None
    AsyncOpenAI = None
    create_client = None

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./interview_agent.db")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
security = HTTPBearer()
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_uuid() -> str:
    return str(uuid.uuid4())


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        if create_client is None:
            raise HTTPException(status_code=500, detail="Install the supabase package first.")
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise HTTPException(status_code=500, detail="SUPABASE_URL or SUPABASE_ANON_KEY is not set.")

        supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        user_response = supabase.auth.get_user(token)
        user = getattr(user_response, "user", None)
        if user is None and isinstance(user_response, dict):
            user = user_response.get("user")
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"sub": user.id, "email": user.email}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


class InterviewSessionDB(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    candidate_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    topic: Mapped[str] = mapped_column(String(180), nullable=False)
    level: Mapped[str] = mapped_column(String(40), nullable=False)
    language: Mapped[str] = mapped_column(String(40), default="English", nullable=False)
    question_mode: Mapped[str] = mapped_column(String(20), default="pre_generated", nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    pause_seconds: Mapped[float] = mapped_column(Float, default=3.5, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="in_progress", nullable=False)
    current_question_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    final_report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    questions: Mapped[List["QuestionDB"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="QuestionDB.order"
    )
    answers: Mapped[List["AnswerDB"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AnswerDB.created_at"
    )


class QuestionDB(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("interview_sessions.id"), index=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    skill_area: Mapped[str] = mapped_column(String(120), default="General", nullable=False)
    difficulty: Mapped[str] = mapped_column(String(40), default="medium", nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="ai_generated", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped[InterviewSessionDB] = relationship(back_populates="questions")
    answers: Mapped[List["AnswerDB"]] = relationship(back_populates="question")


class AnswerDB(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("interview_sessions.id"), index=True, nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id"), index=True, nullable=False)
    question_order: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    audio_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped[InterviewSessionDB] = relationship(back_populates="answers")
    question: Mapped[QuestionDB] = relationship(back_populates="answers")


Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Voice Interview Agent API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_openai_client() -> AsyncOpenAI:
    if AsyncOpenAI is None:
        raise HTTPException(status_code=500, detail="Install the openai package first.")
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set.")
    return AsyncOpenAI()


# -----------------------------
# Request / response models
# -----------------------------

class StartSessionRequest(BaseModel):
    candidate_name: Optional[str] = None
    topic: str = Field(..., min_length=2, max_length=180)
    level: Literal["beginner", "intermediate", "expert"] = "beginner"
    question_count: int = Field(5, ge=1, le=20)
    question_mode: Literal["pre_generated", "dynamic"] = "pre_generated"
    pause_seconds: float = Field(3.5, ge=1.5, le=8.0)

    @field_validator("topic")
    @classmethod
    def clean_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Topic cannot be empty.")
        return value


class TextAnswerRequest(BaseModel):
    question_id: str
    transcript: str = Field(..., min_length=1)


class QuestionOut(BaseModel):
    id: str
    order: int
    text: str
    skill_area: str
    difficulty: str


class AnswerOut(BaseModel):
    id: str
    question_id: str
    question_order: int
    transcript: str
    score: float
    evaluation: dict


class SessionOut(BaseModel):
    session_id: str
    candidate_name: Optional[str]
    topic: str
    level: str
    language: str
    question_mode: str
    question_count: int
    pause_seconds: float
    status: str
    current_question_order: int
    current_question: Optional[QuestionOut]
    answered_count: int
    final_report: Optional[dict] = None


class ResumeGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=10)
    student: Optional[dict] = None


class ResumeEducationItem(BaseModel):
    institution: str
    degree: Optional[str] = None
    fieldOfStudy: Optional[str] = None
    year: Optional[str] = None
    description: Optional[str] = None


class ResumeExperienceItem(BaseModel):
    role: str
    company: str
    period: Optional[str] = None
    description: Optional[str] = None


class ResumeProjectItem(BaseModel):
    name: str
    stack: Optional[str] = None
    description: Optional[str] = None


class GeneratedResumeOut(BaseModel):
    headline: str
    introduction: str
    personal_info: dict
    education: List[ResumeEducationItem]
    experience: List[ResumeExperienceItem]
    skills: List[str]
    projects: List[ResumeProjectItem]
    summary_points: List[str]
    tone: str
    keywords: List[str]


class ResumeGenerationResponse(BaseModel):
    resume: GeneratedResumeOut


# -----------------------------
# Strict JSON schemas for model outputs
# -----------------------------

QUESTION_LIST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "skill_area": {"type": "string"},
                    "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                },
                "required": ["text", "skill_area", "difficulty"],
            },
        }
    },
    "required": ["questions"],
}

ANSWER_EVALUATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "technical_accuracy": {"type": "number", "minimum": 0, "maximum": 100},
        "depth": {"type": "number", "minimum": 0, "maximum": 100},
        "clarity": {"type": "number", "minimum": 0, "maximum": 100},
        "practical_understanding": {"type": "number", "minimum": 0, "maximum": 100},
        "communication": {"type": "number", "minimum": 0, "maximum": 100},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "feedback": {"type": "string"},
        "should_ask_follow_up": {"type": "boolean"},
        "follow_up_question": {"type": ["string", "null"]},
    },
    "required": [
        "score",
        "technical_accuracy",
        "depth",
        "clarity",
        "practical_understanding",
        "communication",
        "strengths",
        "weaknesses",
        "feedback",
        "should_ask_follow_up",
        "follow_up_question",
    ],
}

FINAL_REPORT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overall_score": {"type": "number", "minimum": 0, "maximum": 100},
        "recommendation": {"type": "string", "enum": ["strong_hire", "hire", "borderline", "no_hire"]},
        "detected_level": {"type": "string"},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "details": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question_order": {"type": "integer"},
                    "question": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 100},
                    "comment": {"type": "string"},
                },
                "required": ["question_order", "question", "score", "comment"],
            },
        },
    },
    "required": ["overall_score", "recommendation", "detected_level", "summary", "strengths", "weaknesses", "details"],
}

RESUME_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "introduction": {"type": "string"},
        "personal_info": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "location": {"type": "string"},
                "goal": {"type": "string"},
            },
            "required": ["name", "email", "phone", "location", "goal"],
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": ["string", "null"]},
                    "fieldOfStudy": {"type": ["string", "null"]},
                    "year": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["institution", "degree", "fieldOfStudy", "year", "description"],
            },
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "role": {"type": "string"},
                    "company": {"type": "string"},
                    "period": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["role", "company", "period", "description"],
            },
        },
        "skills": {"type": "array", "items": {"type": "string"}},
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "stack": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["name", "stack", "description"],
            },
        },
        "summary_points": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "headline",
        "introduction",
        "personal_info",
        "education",
        "experience",
        "skills",
        "projects",
        "summary_points",
        "tone",
        "keywords",
    ],
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
    "your",
}

TECH_SIGNAL_WORDS = {
    "api",
    "architecture",
    "async",
    "cache",
    "class",
    "component",
    "database",
    "debug",
    "design",
    "error",
    "flow",
    "implementation",
    "index",
    "latency",
    "logic",
    "migration",
    "model",
    "performance",
    "query",
    "refactor",
    "request",
    "response",
    "schema",
    "security",
    "service",
    "state",
    "testing",
    "thread",
    "transaction",
    "validation",
}


def text_format(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "strict": True,
            "schema": schema,
        }
    }


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _content_tokens(text: str) -> List[str]:
    return [token for token in _tokens(text) if len(token) > 2 and token not in STOPWORDS]


def _contains_any(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _difficulty_multiplier(difficulty: str) -> float:
    return {"easy": 0.92, "medium": 1.0, "hard": 1.08}.get(difficulty.lower(), 1.0)


def _score_answer_locally(question: QuestionDB, transcript: str, evaluation: dict) -> float:
    question_terms = _content_tokens(f"{question.text} {question.skill_area}")
    answer_terms = set(_content_tokens(transcript))
    if not question_terms:
        question_terms = _content_tokens(question.text)

    overlap = len([term for term in question_terms if term in answer_terms])
    topical_coverage = overlap / max(len(set(question_terms)), 1)

    word_count = len(_tokens(transcript))
    length_score = min(word_count / 140, 1.0)
    if word_count < 20:
        length_score *= 0.45
    elif word_count < 45:
        length_score *= 0.75
    elif word_count > 220:
        length_score *= 0.9

    explanation_markers = {
        "because",
        "therefore",
        "for example",
        "for instance",
        "steps",
        "first",
        "second",
        "finally",
        "tradeoff",
        "trade-off",
        "in practice",
        "as a result",
    }
    structure_score = 1.0 if _contains_any(transcript, explanation_markers) else 0.55

    practical_score = 1.0 if _contains_any(transcript, TECH_SIGNAL_WORDS) else 0.65
    if question.skill_area and question.skill_area.lower() not in {"general", "follow-up"}:
        practical_score += 0.1
    practical_score = min(practical_score, 1.0)

    ai_score = float(evaluation.get("score", 0) or 0) / 100.0
    technical = float(evaluation.get("technical_accuracy", 0) or 0) / 100.0
    depth = float(evaluation.get("depth", 0) or 0) / 100.0
    clarity = float(evaluation.get("clarity", 0) or 0) / 100.0
    practical = float(evaluation.get("practical_understanding", 0) or 0) / 100.0
    communication = float(evaluation.get("communication", 0) or 0) / 100.0

    rubric_score = (
        0.30 * technical
        + 0.20 * depth
        + 0.20 * practical
        + 0.15 * clarity
        + 0.15 * communication
    )

    blended = 0.40 * ai_score + 0.35 * rubric_score + 0.15 * topical_coverage + 0.10 * length_score
    blended = (blended * 0.80) + (structure_score * 0.10) + (practical_score * 0.10)
    blended *= _difficulty_multiplier(question.difficulty)

    if "?" in transcript and word_count < 20:
        blended *= 0.92
    if topical_coverage < 0.15 and word_count < 60:
        blended *= 0.85

    return round(max(0.0, min(blended * 100.0, 100.0)), 2)


async def structured_response(prompt: str, schema_name: str, schema: Dict[str, Any], temperature: float = 0.2) -> dict:
    """Call OpenAI with strict structured outputs and return parsed JSON."""
    client = get_openai_client()
    try:
        response = await client.responses.create(
            model=CHAT_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a fair, consistent English-speaking technical interviewer. "
                        "Return only data that matches the requested JSON schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            text=text_format(schema_name, schema),
            temperature=temperature,
        )
        return json.loads(response.output_text)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI structured output error: {exc}") from exc


def _student_to_resume_context(student: Optional[dict], prompt: str) -> dict:
    student = student or {}
    full_name = f"{student.get('firstName', '')} {student.get('lastName', '')}".strip()
    location = ", ".join([part for part in [student.get("city"), student.get("country")] if part]) or "Not provided"
    return {
        "name": full_name or "Student",
        "email": student.get("email") or "Not provided",
        "phone": student.get("phoneNumber") or "Not provided",
        "location": location,
        "goal": student.get("futureGoal") or prompt,
        "bio": student.get("bio") or "",
        "gender": student.get("gender") or "",
        "dob": student.get("dob") or "",
    }


def _fallback_resume(context: dict) -> dict:
    name = context.get("name", "Student")
    goal = context.get("goal", "Build a strong resume")
    return {
        "headline": f"{name} | {goal}",
        "introduction": context.get("bio") or f"Motivated student focused on {goal.lower()}.",
        "personal_info": {
            "name": name,
            "email": context.get("email", "Not provided"),
            "phone": context.get("phone", "Not provided"),
            "location": context.get("location", "Not provided"),
            "goal": goal,
        },
        "education": [],
        "experience": [],
        "skills": [],
        "projects": [],
        "summary_points": [goal],
        "tone": "professional",
        "keywords": [],
    }


async def ai_generate_questions(topic: str, level: str, question_count: int) -> List[dict]:
    prompt = f"""
Create exactly {question_count} English interview questions for this candidate.

Topic: {topic}
Expertise level: {level}

Rules:
- Keep each question spoken-interview friendly.
- Progress from warm-up to deeper reasoning.
- Avoid yes/no questions.
- No answers, only questions.
""".strip()
    data = await structured_response(prompt, "question_list", QUESTION_LIST_SCHEMA, temperature=0.4)
    questions = data.get("questions", [])[:question_count]
    if len(questions) < question_count:
        raise HTTPException(status_code=500, detail="The model returned fewer questions than requested.")
    return questions


async def ai_generate_dynamic_question(session: InterviewSessionDB, previous_answer: AnswerDB, next_order: int) -> dict:
    prompt = f"""
Generate one next English interview question.

Topic: {session.topic}
Expertise level: {session.level}
Question number: {next_order} of {session.question_count}
Previous question: {previous_answer.question_text}
Candidate transcript: {previous_answer.transcript}
Previous evaluation: {json.dumps(previous_answer.evaluation, ensure_ascii=False)}

Rules:
- Ask a relevant follow-up or next question based on the answer.
- Do not repeat earlier questions.
- Keep it concise and suitable for voice.
""".strip()
    data = await structured_response(prompt, "question_list", QUESTION_LIST_SCHEMA, temperature=0.4)
    return data["questions"][0]


async def ai_evaluate_answer(session: InterviewSessionDB, question: QuestionDB, transcript: str) -> dict:
    prompt = f"""
Evaluate this candidate answer fairly.

Interview topic: {session.topic}
Expected level: {session.level}
Language: English
Question {question.order}: {question.text}
Candidate answer transcript: {transcript}

Scoring rubric:
- Technical accuracy: 40%
- Depth of explanation: 20%
- Practical understanding/examples: 15%
- Communication clarity: 15%
- Completeness/confidence: 10%

Be strict but fair. Penalize vague answers, hallucinated facts, or answers that do not address the question.
Return varied scores based on the actual answer quality. Avoid defaulting to a middle score unless the response truly is average.
""".strip()
    evaluation = await structured_response(prompt, "answer_evaluation", ANSWER_EVALUATION_SCHEMA, temperature=0.1)
    evaluation["score"] = _score_answer_locally(question, transcript, evaluation)
    return evaluation


async def ai_final_report(session: InterviewSessionDB, answers: List[AnswerDB]) -> dict:
    answers_payload = [
        {
            "question_order": answer.question_order,
            "question": answer.question_text,
            "transcript": answer.transcript,
            "evaluation": answer.evaluation,
            "score": answer.score,
        }
        for answer in answers
    ]
    prompt = f"""
Create the final interview report.

Topic: {session.topic}
Expected level: {session.level}
Question count: {session.question_count}
Answers and evaluations:
{json.dumps(answers_payload, ensure_ascii=False)}

Return a concise but useful final hiring-style assessment.
""".strip()
    return await structured_response(prompt, "final_report", FINAL_REPORT_SCHEMA, temperature=0.1)


async def transcribe_audio_file(audio_path: Path, filename: str) -> str:
    client = get_openai_client()
    try:
        with audio_path.open("rb") as audio_file:
            result = await client.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL,
                file=(filename, audio_file, "audio/webm"),
                response_format="json",
                language="en",
            )
        text = getattr(result, "text", None) or result.get("text")  # type: ignore[union-attr]
        return (text or "").strip()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI transcription error: {exc}") from exc


# -----------------------------
# DB helpers
# -----------------------------

def get_session_or_404(db: Session, session_id: str) -> InterviewSessionDB:
    session = db.get(InterviewSessionDB, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Interview session not found.")
    return session


def current_question_for(session: InterviewSessionDB) -> Optional[QuestionDB]:
    for question in session.questions:
        if question.order == session.current_question_order:
            return question
    return None


def to_question_out(question: Optional[QuestionDB]) -> Optional[QuestionOut]:
    if question is None:
        return None
    return QuestionOut(
        id=question.id,
        order=question.order,
        text=question.text,
        skill_area=question.skill_area,
        difficulty=question.difficulty,
    )


def to_session_out(session: InterviewSessionDB) -> SessionOut:
    return SessionOut(
        session_id=session.id,
        candidate_name=session.candidate_name,
        topic=session.topic,
        level=session.level,
        language=session.language,
        question_mode=session.question_mode,
        question_count=session.question_count,
        pause_seconds=session.pause_seconds,
        status=session.status,
        current_question_order=session.current_question_order,
        current_question=to_question_out(current_question_for(session)) if session.status == "in_progress" else None,
        answered_count=len(session.answers),
        final_report=session.final_report,
    )


async def finalize_session(db: Session, session: InterviewSessionDB) -> dict:
    answers = list(session.answers)
    if not answers:
        report = {
            "overall_score": 0,
            "recommendation": "no_hire",
            "detected_level": "unknown",
            "summary": "No answers were submitted.",
            "strengths": [],
            "weaknesses": ["Interview had no completed answers."],
            "details": [],
        }
    else:
        report = await ai_final_report(session, answers)
    session.final_report = report
    session.status = "completed"
    session.completed_at = utc_now()
    db.add(session)
    db.commit()
    db.refresh(session)
    return report


async def process_transcript_answer(db: Session, session: InterviewSessionDB, question: QuestionDB, transcript: str, audio_path: Optional[str]) -> dict:
    if session.status != "in_progress":
        raise HTTPException(status_code=400, detail="Interview session is not in progress.")
    if question.order != session.current_question_order:
        raise HTTPException(status_code=409, detail="This question is not the current active question.")

    evaluation = await ai_evaluate_answer(session, question, transcript)
    answer = AnswerDB(
        session_id=session.id,
        question_id=question.id,
        question_order=question.order,
        question_text=question.text,
        transcript=transcript,
        audio_path=audio_path,
        score=float(evaluation.get("score", 0)),
        evaluation=evaluation,
    )
    db.add(answer)
    db.flush()

    next_question: Optional[QuestionDB] = None
    final_report: Optional[dict] = None

    if session.current_question_order >= session.question_count:
        db.commit()
        db.refresh(session)
        final_report = await finalize_session(db, session)
    else:
        session.current_question_order += 1
        if session.question_mode == "dynamic":
            dynamic_q = await ai_generate_dynamic_question(session, answer, session.current_question_order)
            next_question = QuestionDB(
                session_id=session.id,
                order=session.current_question_order,
                text=dynamic_q["text"],
                skill_area=dynamic_q.get("skill_area", "Follow-up"),
                difficulty=dynamic_q.get("difficulty", "medium"),
                source="ai_dynamic",
            )
            db.add(next_question)
        else:
            next_question = current_question_for(session)
        db.add(session)
        db.commit()
        db.refresh(session)
        if next_question is not None:
            db.refresh(next_question)

    return {
        "answer": {
            "id": answer.id,
            "question_id": question.id,
            "question_order": question.order,
            "transcript": transcript,
            "score": answer.score,
            "evaluation": evaluation,
        },
        "session": to_session_out(session).model_dump(),
        "next_question": to_question_out(next_question).model_dump() if next_question else None,
        "final_report": final_report,
    }


# -----------------------------
# REST API
# -----------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "database": DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL}


@app.get("/api/students/me")
async def get_current_student(token=Depends(verify_token)):
    user_id = token.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token does not contain a subject.")

    student = await prisma_db.student.find_first(where={"user_auth_id": user_id})
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    return {"student": student.model_dump() if hasattr(student, "model_dump") else student}


@app.post("/api/resume-builder/generate", response_model=ResumeGenerationResponse)
async def generate_resume(payload: ResumeGenerationRequest):
    student = payload.student

    context = _student_to_resume_context(student, payload.prompt)
    generation_prompt = f"""
Create a polished resume using the student's profile and the user's instructions.
Return only JSON matching the schema.

Student context:
{json.dumps(context, ensure_ascii=False)}

User prompt:
{payload.prompt}

Rules:
- Keep it concise and resume-friendly.
- Include a professional headline, introduction, personal info, education, experience, skills, projects, summary points, tone, and keywords.
- If the student has limited experience, emphasize transferable strengths and project-based evidence instead of inventing employers.
- Do not fabricate degrees, employers, or dates that are not clearly supported by the context.
""".strip()

    try:
        resume = await structured_response(generation_prompt, "resume_generation", RESUME_SCHEMA, temperature=0.35)
    except Exception:
        resume = _fallback_resume(context)

    return {"resume": resume}


@app.post("/api/sessions", response_model=SessionOut)
async def start_session(req: StartSessionRequest, db: Session = Depends(get_db)) -> SessionOut:
    session = InterviewSessionDB(
        candidate_name=req.candidate_name,
        topic=req.topic,
        level=req.level,
        language="English",
        question_mode=req.question_mode,
        question_count=req.question_count,
        pause_seconds=req.pause_seconds,
        status="in_progress",
        current_question_order=1,
    )
    db.add(session)
    db.flush()

    if req.question_mode == "pre_generated":
        questions = await ai_generate_questions(req.topic, req.level, req.question_count)
        for idx, item in enumerate(questions, start=1):
            db.add(
                QuestionDB(
                    session_id=session.id,
                    order=idx,
                    text=item["text"],
                    skill_area=item.get("skill_area", "General"),
                    difficulty=item.get("difficulty", "medium"),
                    source="ai_generated",
                )
            )
    else:
        first_question = (await ai_generate_questions(req.topic, req.level, 1))[0]
        db.add(
            QuestionDB(
                session_id=session.id,
                order=1,
                text=first_question["text"],
                skill_area=first_question.get("skill_area", "General"),
                difficulty=first_question.get("difficulty", "medium"),
                source="ai_dynamic_start",
            )
        )

    db.commit()
    db.refresh(session)
    return to_session_out(session)


@app.get("/api/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    session = get_session_or_404(db, session_id)
    return to_session_out(session)


@app.post("/api/sessions/{session_id}/answer-text")
async def submit_text_answer(session_id: str, req: TextAnswerRequest, db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, session_id)
    question = db.get(QuestionDB, req.question_id)
    if not question or question.session_id != session.id:
        raise HTTPException(status_code=404, detail="Question not found for this session.")
    return await process_transcript_answer(db, session, question, req.transcript.strip(), audio_path=None)


@app.post("/api/sessions/{session_id}/answer-audio")
async def submit_audio_answer(
    session_id: str,
    question_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    session = get_session_or_404(db, session_id)
    question = db.get(QuestionDB, question_id)
    if not question or question.session_id != session.id:
        raise HTTPException(status_code=404, detail="Question not found for this session.")

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    session_dir = UPLOAD_DIR / session.id
    session_dir.mkdir(parents=True, exist_ok=True)
    filename = f"q{question.order}_{make_uuid()}.webm"
    audio_path = session_dir / filename
    audio_path.write_bytes(audio_bytes)

    transcript = await transcribe_audio_file(audio_path, filename)
    if not transcript:
        raise HTTPException(status_code=400, detail="Could not transcribe the answer. Please try again.")
    return await process_transcript_answer(db, session, question, transcript, str(audio_path))


@app.post("/api/sessions/{session_id}/complete")
async def complete_session(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, session_id)
    if session.status == "completed" and session.final_report:
        return {"session": to_session_out(session).model_dump(), "final_report": session.final_report}
    report = await finalize_session(db, session)
    return {"session": to_session_out(session).model_dump(), "final_report": report}


@app.get("/api/sessions/{session_id}/answers")
def list_answers(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, session_id)
    return {
        "session": to_session_out(session).model_dump(),
        "answers": [
            AnswerOut(
                id=a.id,
                question_id=a.question_id,
                question_order=a.question_order,
                transcript=a.transcript,
                score=a.score,
                evaluation=a.evaluation,
            ).model_dump()
            for a in session.answers
        ],
    }


@app.post("/api/realtime/client-secret")
async def create_realtime_client_secret() -> dict:
    """Optional endpoint for a future direct browser WebRTC integration.

    The current UI uses the backend-controlled WebSocket flow because it keeps the
    interview session, scoring, and storage under server control. This endpoint is
    included so you can later connect the browser directly to OpenAI Realtime with
    an ephemeral client secret without exposing your standard OpenAI API key.
    """
    if httpx is None:
        raise HTTPException(status_code=500, detail="Install httpx first.")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set.")
    payload = {
        "session": {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "audio": {"output": {"voice": "marin"}},
        }
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


# -----------------------------
# WebSocket answer streaming
# -----------------------------

@app.websocket("/ws/sessions/{session_id}/answer")
async def stream_answer(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    db = SessionLocal()
    audio_buffer = bytearray()
    question_id: Optional[str] = None
    mime_type = "audio/webm"

    try:
        session = get_session_or_404(db, session_id)
        current_q = current_question_for(session)
        if not current_q:
            await websocket.send_json({"type": "error", "message": "No active question."})
            await websocket.close()
            return
        await websocket.send_json(
            {
                "type": "ready",
                "session": to_session_out(session).model_dump(),
                "current_question": to_question_out(current_q).model_dump(),
            }
        )

        while True:
            message = await websocket.receive()
            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON message."})
                    continue

                msg_type = payload.get("type")
                if msg_type == "answer_start":
                    question_id = payload.get("question_id")
                    mime_type = payload.get("mime_type", "audio/webm")
                    audio_buffer = bytearray()
                    await websocket.send_json({"type": "answer_started"})

                elif msg_type == "answer_end":
                    if not question_id:
                        await websocket.send_json({"type": "error", "message": "Question ID missing."})
                        continue
                    session = get_session_or_404(db, session_id)
                    question = db.get(QuestionDB, question_id)
                    if not question or question.session_id != session.id:
                        await websocket.send_json({"type": "error", "message": "Question not found for this session."})
                        continue
                    if not audio_buffer:
                        await websocket.send_json({"type": "error", "message": "No audio received."})
                        continue

                    await websocket.send_json({"type": "processing", "message": "Transcribing and evaluating answer."})
                    session_dir = UPLOAD_DIR / session.id
                    session_dir.mkdir(parents=True, exist_ok=True)
                    extension = "webm" if "webm" in mime_type else "wav"
                    filename = f"q{question.order}_{make_uuid()}.{extension}"
                    audio_path = session_dir / filename
                    audio_path.write_bytes(bytes(audio_buffer))

                    transcript = await transcribe_audio_file(audio_path, filename)
                    if not transcript:
                        await websocket.send_json({"type": "error", "message": "Could not transcribe audio."})
                        continue

                    result = await process_transcript_answer(db, session, question, transcript, str(audio_path))
                    if result.get("final_report"):
                        await websocket.send_json({"type": "interview_complete", **result})
                    else:
                        await websocket.send_json({"type": "answer_processed", **result})
                    audio_buffer = bytearray()
                    question_id = None

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                else:
                    await websocket.send_json({"type": "error", "message": f"Unsupported message type: {msg_type}"})

            elif "bytes" in message and message["bytes"] is not None:
                audio_buffer.extend(message["bytes"])

    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "message": exc.detail})
    except Exception as exc:  # pragma: no cover
        await websocket.send_json({"type": "error", "message": str(exc)})
    finally:
        db.close()
