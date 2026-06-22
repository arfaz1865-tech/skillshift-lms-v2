"""SkillShift LMS Backend - FastAPI application entry point."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Literal, Optional
from dotenv import load_dotenv
load_dotenv()

import re
import jwt
import stripe
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy import text
from question_bank import QUESTION_BANK
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import io
from pypdf import PdfReader

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local")

try:
    import httpx
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    httpx = None
    AsyncOpenAI = None

from common.auth import ALGORITHM, SECRET_KEY
from common.database import db as prisma_db

security = HTTPBearer()

# Pehle LOCAL_DATABASE_URL check karega, agar nahi milti to fallback karega DATABASE_URL par
DATABASE_URL = os.getenv("LOCAL_DATABASE_URL", os.getenv("DATABASE_URL", "sqlite:///./interview_agent.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
TRANSCRIBE_MODEL = os.getenv("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3")
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")  # OpenAI-only, neeche note dekho


stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "skillshiftlms@gmail.com")

COMPANY_PLAN_LIMITS = {"free": 3, "starter": 20, "growth": 999999}
COMPANY_PLAN_PRICES_CENTS = {"starter": 1900, "growth": 5900}  # free ke liye checkout nahi hota
COMPANY_PLAN_LABELS = {"free": "Free", "starter": "Starter", "growth": "Growth"}

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}


origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    os.getenv("FRONTEND_URL", ""),  # Netlify URL injected via env var
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in origins if o],  # filters out empty strings
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ROBUST URL CLEANER ---
SQLALCHEMY_DATABASE_URL = DATABASE_URL
if SQLALCHEMY_DATABASE_URL and not SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    if "?" in SQLALCHEMY_DATABASE_URL:
        SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.split("?")[0]

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_uuid() -> str:
    return str(uuid.uuid4())


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

class CourseDB(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    instructorId: Mapped[str] = mapped_column(String(36), nullable=False)
    isCompanyCourse: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    shortDescription: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="DRAFT")
    level: Mapped[Optional[str]] = mapped_column(String, default="beginner")
    language: Mapped[Optional[str]] = mapped_column(String, default="English")
    thumbnail: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    totalLessons: Mapped[int] = mapped_column(Integer, default=0)
    totalDurationMinutes: Mapped[int] = mapped_column(Integer, default=0)
    pricingType: Mapped[str] = mapped_column(String, default="FREE")
    price: Mapped[float] = mapped_column(Float, default=0)
    discountPrice: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)





class ModuleDB(Base):
    __tablename__ = "courseModules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    courseId: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=1)


class LessonDB(Base):
    __tablename__ = "lessons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    courseId: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id"), nullable=False)
    moduleId: Mapped[str] = mapped_column(String(36), ForeignKey("courseModules.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, default="video")
    contentUrl: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    order: Mapped[int] = mapped_column(Integer, default=1)
    quiz: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    assignment: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

class EnrollmentDB(Base):
    __tablename__ = "enrollments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    courseId: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id"), nullable=False)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    progressPercentage: Mapped[float] = mapped_column(Float, default=0)
    completedLessons: Mapped[int] = mapped_column(Integer, default=0)
    enrolledAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)



class InvoiceDB(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    courseId: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id"), nullable=False)
    enrollmentId: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("enrollments.id"), nullable=True)
    paymentId: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    invoiceType: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    invoiceStatus: Mapped[Optional[str]] = mapped_column(String, default="paid")
    invoiceMethod: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    invoiceGateway: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    transactionId: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    gatewayTransactionId: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    invoiceAmount: Mapped[float] = mapped_column(Float, default=0)
    taxAmount: Mapped[float] = mapped_column(Float, default=0)
    totalAmount: Mapped[float] = mapped_column(Float, default=0)
    discountApplied: Mapped[float] = mapped_column(Float, default=0)
    currencyType: Mapped[Optional[str]] = mapped_column(String, default="usd")
    isSuccessful: Mapped[bool] = mapped_column(Boolean, default=True)
    receiptUrl: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    invoiceDate: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    invoiceCompletedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)




class LessonProgressDB(Base):
    __tablename__ = "lesson_progress"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    enrollmentId: Mapped[str] = mapped_column(String(36), ForeignKey("enrollments.id"), nullable=False)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    courseId: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id"), nullable=False)
    lessonId: Mapped[str] = mapped_column(String(36), ForeignKey("lessons.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, default="NOT_STARTED")
    progressPercentage: Mapped[float] = mapped_column(Float, default=0)
    watchTimeSeconds: Mapped[int] = mapped_column(Integer, default=0)
    completedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class QuizAttemptDB(Base):
    __tablename__ = "quiz_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    lessonId: Mapped[str] = mapped_column(String(36), ForeignKey("lessons.id"), nullable=False)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0)
    percentage: Mapped[float] = mapped_column(Float, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    answers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    submittedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssignmentSubmissionDB(Base):
    __tablename__ = "assignment_submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    lessonId: Mapped[str] = mapped_column(String(36), ForeignKey("lessons.id"), nullable=False)
    enrollmentId: Mapped[str] = mapped_column(String(36), ForeignKey("enrollments.id"), nullable=False)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    answerText: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submissionText: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachmentUrl: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="SUBMITTED")
    marks: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submittedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    gradedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class RoadmapDB(Base):
    __tablename__ = "roadmaps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    studentId: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # null = admin/public roadmap
    roadmapTitle: Mapped[str] = mapped_column(String, nullable=False)
    roadmapDescription: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    roadmapStatus: Mapped[str] = mapped_column(String, default="ACTIVE")
    totalCourse: Mapped[int] = mapped_column(Integer, default=0)
    difficultyLevel: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    estimatedDuration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    courseSequence: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    currentSkillsInput: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    goalInput: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timeCommitmentInput: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aiReasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    isAiGenerated: Mapped[bool] = mapped_column(Boolean, default=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)



class CourseChatMessageDB(Base):
    __tablename__ = "course_chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citedLessons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    grounded: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class ResumeDB(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    templateId: Mapped[str] = mapped_column(String(40), default="modern")
    sourceMode: Mapped[str] = mapped_column(String(20), default="ai_generate")
    inputText: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resumeData: Mapped[dict] = mapped_column(JSON, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

class CompanyDB(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    instructorId: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invitationLimit: Mapped[int] = mapped_column(Integer, default=0)
    planTier: Mapped[str] = mapped_column(String, default="free")
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class CompanyEmployeeDB(Base):
    __tablename__ = "company_employees"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    companyId: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False)
    studentId: Mapped[str] = mapped_column(String(36), nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    invitedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompanyCourseDB(Base):
    __tablename__ = "company_courses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    companyId: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False)
    courseId: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, default="own")  # "own" = company ne khud banaya, "selected" = catalog se select kiya
    addedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class PaymentDB(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    payerType: Mapped[str] = mapped_column(String, nullable=False)  # "student" ya "company"
    payerId: Mapped[str] = mapped_column(String(36), nullable=False)
    payerName: Mapped[str] = mapped_column(String, nullable=True)
    paymentType: Mapped[str] = mapped_column(String, nullable=False)  # course_purchase / company_course / company_upgrade
    referenceTitle: Mapped[str] = mapped_column(String, nullable=True)  # course title ya plan name
    amount: Mapped[float] = mapped_column(Float, default=0)
    stripeSessionId: Mapped[str] = mapped_column(String, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await prisma_db.connect()
    yield
    await prisma_db.disconnect()


app = FastAPI(
    title="SkillShift LMS API",
    description="Learning Management System API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
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
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not set.")
    return AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")



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

class CompanyRegisterRequest(BaseModel):
    instructorId: str
    name: str
    description: Optional[str] = ""


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

_BANK_TEXTS = [f"{q['skill_area']} {q['text']}" for q in QUESTION_BANK]
_BANK_VECTORIZER = TfidfVectorizer(stop_words="english")
_BANK_MATRIX = _BANK_VECTORIZER.fit_transform(_BANK_TEXTS)


def retrieve_bank_questions(topic: str, level: str, limit: int = 6) -> List[dict]:
    query_vector = _BANK_VECTORIZER.transform([f"{topic} {level}"])
    scores = cosine_similarity(query_vector, _BANK_MATRIX)[0]
    ranked_indices = scores.argsort()[::-1]
    results = []
    for idx in ranked_indices:
        if scores[idx] <= 0:
            break
        results.append(QUESTION_BANK[idx])
        if len(results) >= limit:
            break
    return results


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
        "voice_clarity_score": {"type": "number", "minimum": 0, "maximum": 100},
        "delivery_feedback": {"type": "string"},
        "visual_engagement_score": {"type": "number", "minimum": 0, "maximum": 100},
        "visual_feedback": {"type": "string"},
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
        "voice_clarity_score",
        "delivery_feedback",
        "visual_engagement_score",
        "visual_feedback",
        "strengths",
        "weaknesses",
        "feedback",
        "should_ask_follow_up",
        "follow_up_question",
    ],
}

FILLER_WORD_PATTERN = re.compile(
    r"\b(um+|uh+|erm+|like|you know|i mean|kind of|sort of|basically|actually)\b", re.IGNORECASE
)


def count_filler_words(transcript: str) -> int:
    return len(FILLER_WORD_PATTERN.findall(transcript))


def compute_words_per_minute(transcript: str, duration_seconds: Optional[float]) -> Optional[float]:
    if not duration_seconds or duration_seconds <= 0:
        return None
    word_count = len(transcript.split())
    return (word_count / duration_seconds) * 60



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


def text_format(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}


    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "instructorId": c.instructorId,
        "invitationLimit": c.invitationLimit,
        "planTier": c.planTier,
        "planLabel": COMPANY_PLAN_LABELS.get(c.planTier, "Free"),
    }




async def structured_response(prompt: str, schema_name: str, schema: Dict[str, Any], temperature: float = 0.2) -> dict:
    client = get_openai_client()
    try:
        response = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a fair, consistent English-speaking technical interviewer. "
                        "Respond with ONLY a valid JSON object (no extra text, no markdown) matching exactly "
                        f"this JSON schema:\n{json.dumps(schema)}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        return json.loads(response.choices[0].message.content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI structured output error: {exc}") from exc

COURSE_CHAT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "grounded": {"type": "boolean"},
        "cited_lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "lesson_title": {"type": "string"},
                    "course_title": {"type": "string"},
                },
                "required": ["lesson_title", "course_title"],
            },
        },
    },
    "required": ["answer", "grounded", "cited_lessons"],
}


def retrieve_relevant_lessons_global(lessons_with_course: List[tuple], query: str, limit: int = 5) -> List[tuple]:
    corpus = [f"{course.title} {lesson.title} {lesson.content or ''}".strip() for lesson, course in lessons_with_course]
    if not any(corpus):
        return []
    vectorizer = TfidfVectorizer(stop_words="english")
    try:
        matrix = vectorizer.fit_transform(corpus)
        query_vector = vectorizer.transform([query])
    except ValueError:
        return []
    scores = cosine_similarity(query_vector, matrix)[0]
    ranked_indices = scores.argsort()[::-1]
    results = []
    for idx in ranked_indices:
        if scores[idx] <= 0:
            break
        results.append(lessons_with_course[idx])
        if len(results) >= limit:
            break
    return results


async def ai_student_assistant_answer(
    enrollments_summary: List[dict],
    retrieved: List[tuple],
    history: List["CourseChatMessageDB"],
    question: str,
) -> dict:
    enrollment_lines = "\n".join(
        f"- {e['title']} ({e['completedLessons']}/{e['totalLessons']} lessons completed, {e['completionPercentage']}%)"
        for e in enrollments_summary
    ) or "Not enrolled in any course yet."

    excerpts = (
        "\n\n".join(
            f"--- Course: {course.title} | Lesson: {lesson.title} ---\n{(lesson.content or '')[:1500]}"
            for lesson, course in retrieved
        )
        or "No matching lesson content was found."
    )
    history_text = (
        "\n".join(f"{'Student' if m.role == 'user' else 'Assistant'}: {m.content}" for m in history[-6:])
        or "No prior conversation."
    )

    prompt = f"""
You are a helpful personal learning assistant for a student on an online learning platform. You know
which courses they're enrolled in and their progress. Answer using ONLY the lesson excerpts provided
below plus the student's enrollment/progress info. Do not use outside knowledge for course-content
questions. If the excerpts don't cover the question, say so honestly.

Student's enrolled courses and progress:
{enrollment_lines}

Relevant lesson excerpts found across their enrolled courses:
{excerpts}

Recent conversation:
{history_text}

Student's question: {question}

If you used specific lesson content to answer, list it in cited_lessons with the exact lesson_title
and course_title. If you couldn't answer from the material, set grounded to false and leave
cited_lessons empty. You can still use the enrollment/progress info (which courses they're in, how
far along they are) to give general guidance even when grounded is false.
""".strip()
    return await structured_response(prompt, "course_chat_answer", COURSE_CHAT_SCHEMA, temperature=0.2)


RESUME_GENERATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "headline": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "introduction": {"type": "string"},
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": "string"},
                    "fieldOfStudy": {"type": "string"},
                    "year": {"type": "string"},
                    "description": {"type": "string"},
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
                    "period": {"type": "string"},
                    "description": {"type": "string"},
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
                    "stack": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "stack", "description"],
            },
        },
    },
    "required": [
        "name", "headline", "email", "phone", "location", "introduction",
        "education", "experience", "skills", "projects",
    ],
}

def _apply_profile_identity(resume_data: dict, student_profile: dict) -> dict:
    if not student_profile:
        return resume_data
    full_name = f"{student_profile.get('firstName', '')} {student_profile.get('lastName', '')}".strip()
    if full_name:
        resume_data["name"] = full_name
    if student_profile.get("email"):
        resume_data["email"] = student_profile["email"]
    if student_profile.get("phoneNumber"):
        resume_data["phone"] = student_profile["phoneNumber"]
    location = ", ".join(filter(None, [student_profile.get("city"), student_profile.get("country")]))
    if location:
        resume_data["location"] = location
    if student_profile.get("futureGoal") and not resume_data.get("headline"):
        resume_data["headline"] = student_profile["futureGoal"]
    return resume_data

async def ai_generate_resume_from_profile(student_profile: dict, prompt: str) -> dict:
    prompt_text = f"""
Generate a complete, professional one-page resume in JSON for this student.

Use any real details from their profile below (name, contact info, stated goal) as-is. For the resume
body (introduction, education, experience, skills, projects), use the profile facts if present, but if
the profile is sparse or missing details, write strong, realistic example content appropriate for the
role/focus described in the instructions below - the kind of entries a real student in that field might
plausibly have. This is a first draft the student will personalize, so prefer a complete, useful resume
over an empty one. Never leave education, experience, skills, or projects as empty arrays - always
populate them with sensible content matching the requested role and level.

For email, phone, and location specifically: if a value is not available in the profile, return an
empty string for it - never write "N/A", "Not Available", or similar placeholder text.

Student profile:
{json.dumps(student_profile, ensure_ascii=False)}

Instructions from the student: {prompt}
""".strip()
    resume_data = await structured_response(prompt_text, "resume_generation", RESUME_GENERATION_SCHEMA, temperature=0.5)
    return _apply_profile_identity(resume_data, student_profile)

async def ai_generate_resume_from_custom_text(raw_text: str, student_profile: Optional[dict] = None) -> dict:
    profile_text = json.dumps(student_profile, ensure_ascii=False) if student_profile else "No additional profile provided."
    prompt_text = f"""
The student pasted their own resume content or described the format/content they want. Restructure it
into the standard resume JSON schema below, preserving their actual facts, wording, and achievements as
closely as possible. Do not invent new experience, education, or projects beyond what's given. Only
clean up grammar and formatting.

Student's pasted content / format instructions:
{raw_text}

Known profile info (use only to fill obvious gaps like name/email/phone if missing above):
{profile_text}
""".strip()
    return await structured_response(prompt_text, "resume_generation", RESUME_GENERATION_SCHEMA, temperature=0.2)


async def ai_generate_resume_with_format_reference(
    student_profile: dict, format_reference_text: str, prompt: Optional[str]
) -> dict:
    prompt_text = f"""
Generate a complete, professional resume in JSON for this student, using their REAL profile facts
below for all content (name, contact info, education, experience, skills, projects).

Use the uploaded reference resume text below ONLY as a structural and stylistic guide - notice its
section ordering, level of detail, tone, and formatting conventions, and mirror that style. Do NOT
copy any facts, names, companies, schools, or achievements from the reference text - those belong to
a different person and must never appear in the output.

Student profile (the only source of truth for facts):
{json.dumps(student_profile, ensure_ascii=False)}

Reference resume text (style/format guide only, ignore its actual facts):
{format_reference_text[:4000]}

Additional instructions from the student: {prompt or 'None'}

If the student profile is sparse, write strong, realistic example content appropriate for an
entry-level role implied by the profile/instructions, formatted in the style of the reference. Never
leave education, experience, skills, or projects as empty arrays.
""".strip()
    resume_data = await structured_response(prompt_text, "resume_generation", RESUME_GENERATION_SCHEMA, temperature=0.4)
    return _apply_profile_identity(resume_data, student_profile)


async def ai_generate_questions(topic: str, level: str, question_count: int) -> List[dict]:
    reference_questions = retrieve_bank_questions(topic, level, limit=8)
    reference_text = (
        "\n".join(f"- {q['text']} (area: {q['skill_area']}, difficulty: {q['difficulty']})" for q in reference_questions)
        if reference_questions
        else "No closely matching reference questions found."
    )

    prompt = f"""
Create exactly {question_count} English interview questions for this candidate.

Topic: {topic}
Expertise level: {level}

Here are real, commonly-asked technical interview questions on related topics, for reference on the
kind of questions actually asked in real interviews (do not copy them verbatim, write fresh tailored
versions inspired by their style and difficulty):
{reference_text}

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


async def ai_evaluate_answer(
    session: InterviewSessionDB,
    question: QuestionDB,
    transcript: str,
    duration_seconds: Optional[float] = None,
    words_per_minute: Optional[float] = None,
    filler_word_count: Optional[int] = None,
    visual_signals: Optional[dict] = None,
) -> dict:
    delivery_lines = []
    if duration_seconds:
        delivery_lines.append(f"Answer duration: {duration_seconds:.1f} seconds")
    if words_per_minute:
        delivery_lines.append(
            f"Speaking pace: {words_per_minute:.0f} words per minute "
            "(natural conversational pace is roughly 120-160 wpm)"
        )
    if filler_word_count is not None:
        delivery_lines.append(f"Filler words detected (um, uh, like, you know, etc.): {filler_word_count}")
    delivery_signals = "\n".join(delivery_lines) or "No voice delivery signals available (text-based answer)."

    visual_lines = []
    if visual_signals:
        if visual_signals.get("face_presence_ratio") is not None:
            visual_lines.append(
                f"Face visible in camera for {visual_signals['face_presence_ratio'] * 100:.0f}% of the answer"
            )
        if visual_signals.get("avg_smile") is not None:
            visual_lines.append(f"Average smile intensity: {visual_signals['avg_smile']:.2f} (0=neutral, 1=big smile)")
        if visual_signals.get("avg_brow_tension") is not None:
            visual_lines.append(
                f"Average brow tension/furrow: {visual_signals['avg_brow_tension']:.2f} (0=relaxed, 1=very tense)"
            )
        if visual_signals.get("blink_rate_per_minute") is not None:
            visual_lines.append(
                f"Blink rate: {visual_signals['blink_rate_per_minute']:.0f} blinks/minute "
                "(normal resting rate is roughly 15-20/minute; much higher can indicate nervousness)"
            )
    visual_signal_text = "\n".join(visual_lines) or "No camera/visual signals available."

    prompt = f"""
Evaluate this candidate answer fairly.

Interview topic: {session.topic}
Expected level: {session.level}
Language: English
Question {question.order}: {question.text}
Candidate answer transcript: {transcript}

Voice delivery signals:
{delivery_signals}

Camera/visual engagement signals (from real-time face landmark tracking, not raw video):
{visual_signal_text}

Scoring rubric:
- Technical accuracy: 30%
- Depth of explanation: 15%
- Practical understanding/examples: 15%
- Communication clarity: 10%
- Voice delivery (pace, hesitation, confidence): 10%
- Visual engagement (presence, composure, tension cues): 10%
- Completeness/confidence: 10%

Be strict but fair. Penalize vague answers, hallucinated facts, answers that do not address the
question, or signs of nervousness/hesitation in the delivery or visual signals. If voice or visual
signals are unavailable, score that dimension a neutral 70 and say so in the relevant feedback field.
""".strip()
    return await structured_response(prompt, "answer_evaluation", ANSWER_EVALUATION_SCHEMA, temperature=0.1)




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
    return QuestionOut(id=question.id, order=question.order, text=question.text, skill_area=question.skill_area, difficulty=question.difficulty)


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


async def process_transcript_answer(db: Session, session: InterviewSessionDB, question: QuestionDB, transcript: str, audio_path: Optional[str], duration_seconds: Optional[float] = None, visual_signals: Optional[dict] = None) -> dict:
    if session.status != "in_progress":
        raise HTTPException(status_code=400, detail="Interview session is not in progress.")
    if question.order != session.current_question_order:
        raise HTTPException(status_code=409, detail="This question is not the current active question.")

    words_per_minute = compute_words_per_minute(transcript, duration_seconds)
    filler_word_count = count_filler_words(transcript)
    evaluation = await ai_evaluate_answer(session, question, transcript, duration_seconds, words_per_minute, filler_word_count, visual_signals)
    score = float(evaluation.get("score", 0.0))

    answer = AnswerDB(
        session_id=session.id,
        question_id=question.id,
        question_order=question.order,
        question_text=question.text,
        transcript=transcript,
        audio_path=audio_path,
        score=score,
        evaluation=evaluation,
    )
    db.add(answer)

    next_order = session.current_question_order + 1
    session.current_question_order = next_order
    db.add(session)
    db.commit()
    db.refresh(session)
    db.refresh(answer)

    final_report = None
    next_question_out = None

    if next_order > session.question_count:
        final_report = await finalize_session(db, session)
    else:
        if session.question_mode == "dynamic":
            next_question_data = await ai_generate_dynamic_question(session, answer, next_order)
            next_question = QuestionDB(
                session_id=session.id,
                order=next_order,
                text=next_question_data["text"],
                skill_area=next_question_data.get("skill_area", "General"),
                difficulty=next_question_data.get("difficulty", "medium"),
                source="ai_dynamic",
            )
            db.add(next_question)
            db.commit()
            db.refresh(next_question)
        else:
            next_question = next((q for q in session.questions if q.order == next_order), None)
        next_question_out = to_question_out(next_question).model_dump() if next_question else None

    return {
        "answer": AnswerOut(
            id=answer.id,
            question_id=answer.question_id,
            question_order=answer.question_order,
            transcript=answer.transcript,
            score=answer.score,
            evaluation=answer.evaluation,
        ).model_dump(),
        "session": to_session_out(session).model_dump(),
        "next_question": next_question_out,
        "final_report": final_report,
    }


async def send_invite_email(to_email: str, company_name: str, student_name: str = "") -> None:
    """Resend API se employee ko company invite email bhejta hai. Fail ho jaye to bhi
    invite create hona nahi rukta - sirf error console mein print hoti hai."""
    if not RESEND_API_KEY or httpx is None:
        print(f"[invite email skipped] RESEND_API_KEY missing or httpx unavailable for {to_email}")
        return
 
    greeting = f"Hi {student_name}," if student_name else "Hi,"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #1c2536;">You've been invited!</h2>
      <p>{greeting}</p>
      <p><strong>{company_name}</strong> has invited you to access their courses on
      <strong>SkillShift LMS</strong>. You now have free access to the courses they've
      assigned for your training.</p>
      <p>Log in to your SkillShift LMS student account to start learning.</p>
      <p style="color: #888; font-size: 12px; margin-top: 24px;">
        If you weren't expecting this invitation, you can safely ignore this email.
      </p>
    </div>
    """.strip()
 
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"SkillShift LMS <{RESEND_FROM_EMAIL}>",
                    "to": [to_email],
                    "subject": f"You've been invited to {company_name} on SkillShift LMS",
                    "html": html_body,
                },
            )
        if response.status_code >= 400:
            print(f"[invite email error] {response.status_code}: {response.text}")
    except Exception as exc:
        print(f"[invite email exception] {exc}")
 
 

@app.get("/protected")
async def protected_route(token_data: dict = Depends(verify_token)):
    return {"message": "This is a protected route", "user": token_data}


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "SkillShift LMS API"}


@app.post("/api/sessions", response_model=SessionOut)
async def start_session(req: StartSessionRequest, db: Session = Depends(get_db)) -> SessionOut:
    session = InterviewSessionDB(
        candidate_name=req.candidate_name,
        topic=req.topic,
        level=req.level,
        question_mode=req.question_mode,
        question_count=req.question_count,
        pause_seconds=req.pause_seconds,
        language="English",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    if req.question_mode == "pre_generated":
        questions = await ai_generate_questions(req.topic, req.level, req.question_count)
        for index, item in enumerate(questions, start=1):
            db.add(
                QuestionDB(
                    session_id=session.id,
                    order=index,
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
    return to_session_out(get_session_or_404(db, session_id))


@app.post("/api/sessions/{session_id}/answer-text")
async def submit_text_answer(session_id: str, req: TextAnswerRequest, db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, session_id)
    question = db.get(QuestionDB, req.question_id)
    if not question or question.session_id != session.id:
        raise HTTPException(status_code=404, detail="Question not found for this session.")
    return await process_transcript_answer(db, session, question, req.transcript.strip(), audio_path=None)


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
        await websocket.send_json({"type": "ready", "session": to_session_out(session).model_dump(), "current_question": to_question_out(current_q).model_dump()})

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

                    duration_seconds = payload.get("duration_seconds")
                    visual_signals = payload.get("visual_signals")

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

                    result = await process_transcript_answer(db, session, question, transcript, str(audio_path), duration_seconds, visual_signals)
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
        print(f"[answer websocket] HTTPException: {exc.detail}")
        try:
            await websocket.send_json({"type": "error", "message": exc.detail})
        except Exception:
            pass
    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        db.close()



class RegisterInstructorRequest(BaseModel):
    firstName: str
    lastName: str
    email: str
    password: str
    phoneNumber: Optional[str] = None
    accountType: str = "individual"

@app.post("/api/instructor/register")
async def register_instructor(req: RegisterInstructorRequest):
    try:
        # 🛠️ 'prisma_db' use kar rahe hain kyunke aapka instance isi naam se imported hai
        existing_instructor = await prisma_db.instructor.find_first(
            where={"email": req.email}
        )
        
        if existing_instructor:
            raise HTTPException(status_code=400, detail="Email already registered.")

        # Prisma Client create command using the correct instance
        new_instructor = await prisma_db.instructor.create(
            data={
                "firstName": req.firstName,
                "lastName": req.lastName,
                "email": req.email,
                "phoneNumber": req.phoneNumber,
                "accountType": req.accountType,
                "accountStatus": "active"
            }
        )

        return {
            "status": "success", 
            "message": "Instructor registered successfully via Prisma Client!",
            "instructor_id": new_instructor.id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prisma Database Error: {str(e)}")


@app.get("/api/instructor/profile")
async def get_instructor_profile(email: str):
    try:
        instructor = await prisma_db.instructor.find_first(
            where={"email": email}
        )
        if not instructor:
            raise HTTPException(status_code=404, detail="Instructor profile not found.")
        return instructor
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class RegisterStudentRequest(BaseModel):
    firstName: str
    lastName: str
    email: str
    password: str
    phoneNumber: Optional[str] = None

@app.post("/api/student/register")
async def register_student(req: RegisterStudentRequest):
    try:
        # 1. Check karein agar student email pehle se exist karti hai
        # (Aapke schema.prisma ke model name lowercase lowercase: prisma_db.student)
        existing_student = await prisma_db.student.find_first(
            where={"email": req.email}
        )
        
        if existing_student:
            raise HTTPException(status_code=400, detail="Email already registered.")

        # 2. Direct database mein records save karein bypass kar ke Supabase restrictions ko
        new_student = await prisma_db.student.create(
            data={
                "firstName": req.firstName,
                "lastName": req.lastName,
                "email": req.email,
                "phoneNumber": req.phoneNumber,
                "accountStatus": "active"
                # user_auth_id field agar nullable hai to handle ho jayegi automatically
            }
        )

        return {
            "status": "success", 
            "message": "Student registered successfully via local backend!",
            "student_id": new_student.id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Student Database Error: {str(e)}")

@app.get("/api/student/profile")
async def get_student_profile(email: str):
    try:
        # Aapke schema.prisma ke mutabiq student model access ho raha hai
        student = await prisma_db.student.find_first(
            where={"email": email}
        )
        if not student:
            raise HTTPException(status_code=404, detail="Student profile not found locally.")
        return student
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

@app.get("/api/instructor/profile")
async def get_instructor_profile(email: str):
    try:
        instructor = await prisma_db.instructor.find_first(
            where={"email": email}
        )
        if not instructor:
            raise HTTPException(status_code=404, detail="Instructor profile not found locally.")
        return instructor
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

@app.get("/api/admin/profile")
async def get_admin_profile(email: str):
    try:
        # Aapke schema.prisma ke mutabiq admin table lookup
        admin = await prisma_db.admin.find_first(
            where={"email": email}
        )
        if not admin:
            raise HTTPException(status_code=404, detail="Admin profile not found locally.")
        return admin
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")       


# --- Payload schemas matching the frontend payload shape ---

class QuizOptionPayload(BaseModel):
    optionText: str = ""
    isCorrect: bool = False

class QuizQuestionPayload(BaseModel):
    question: str = ""
    questionType: str = "SINGLE_CHOICE"
    marks: int = 1
    options: List[QuizOptionPayload] = []

class QuizPayload(BaseModel):
    title: str = ""
    description: str = ""
    passingPercentage: float = 70
    durationMinutes: int = 0
    attemptsAllowed: int = 1
    showCorrectAnswers: bool = True
    questions: List[QuizQuestionPayload] = []

class AssignmentPayload(BaseModel):
    title: str = ""
    description: str = ""
    instructions: str = ""
    maxMarks: int = 100
    dueDate: Optional[str] = None
    allowLateSubmission: bool = True

class LessonPayload(BaseModel):
    title: str = ""
    type: str = "video"
    contentUrl: Optional[str] = ""
    content: Optional[str] = ""
    duration: int = 0
    order: int = 1
    quiz: Optional[QuizPayload] = None
    assignment: Optional[AssignmentPayload] = None

class ModulePayload(BaseModel):
    title: str = ""
    description: Optional[str] = ""
    order: int = 1
    lessons: List[LessonPayload] = []

class CoursePayload(BaseModel):
    title: str
    instructorId: Optional[str] = None
    description: Optional[str] = ""
    shortDescription: Optional[str] = ""
    status: Optional[str] = "DRAFT"
    level: Optional[str] = "beginner"
    language: Optional[str] = "English"
    thumbnail: Optional[str] = ""
    totalLessons: Optional[int] = 0
    totalDurationMinutes: Optional[int] = 0
    pricingType: Optional[str] = "FREE"
    price: Optional[float] = 0
    discountPrice: Optional[float] = None
    tags: Optional[List[str]] = []
    modules: List[ModulePayload] = []


# =====================================================================
# STEP 1: Top of main.py mein, baqi imports ke sath ye add karo
# (agar httpx installed nahi hai to terminal mein:
#  pip install httpx --break-system-packages)
# =====================================================================
#
# import httpx
# import json as json_lib


# =====================================================================
# STEP 2: File ke end mein ye naya route add karo
# =====================================================================

@app.get("/api/student/jobs")
async def get_student_jobs(studentId: str, db: Session = Depends(get_db)):
    # 1. Student ke enrolled courses nikalo
    enrollments = db.query(EnrollmentDB).filter(EnrollmentDB.studentId == studentId).all()
    course_ids = list({e.courseId for e in enrollments})

    if not course_ids:
        return {"jobs": [], "keywords": []}

    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all()

    # 2. Course titles/tags se search keywords banao
    keywords = set()
    for c in courses:
        if c.title:
            keywords.add(c.title.strip())
        if getattr(c, "tags", None):
            try:
                tag_list = c.tags if isinstance(c.tags, list) else json_lib.loads(c.tags)
                for t in tag_list:
                    if t:
                        keywords.add(str(t).strip())
            except Exception:
                pass

    keywords = list(keywords)[:5]  # zyada API calls se bachne ke liye limit

    # 3. Har keyword ke liye Remotive (free, no API key) se jobs mangwao
    all_jobs = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for kw in keywords:
            try:
                resp = await client.get(
                    "https://remotive.com/api/remote-jobs", params={"search": kw, "limit": 10}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for job in data.get("jobs", []):
                        all_jobs[job["id"]] = {
                            "id": job["id"],
                            "title": job.get("title"),
                            "company": job.get("company_name"),
                            "companyLogo": job.get("company_logo"),
                            "category": job.get("category"),
                            "jobType": job.get("job_type"),
                            "location": job.get("candidate_required_location"),
                            "salary": job.get("salary") or None,
                            "url": job.get("url"),
                            "publicationDate": job.get("publication_date"),
                            "matchedKeyword": kw,
                        }
            except Exception:
                continue

    jobs_list = sorted(
        all_jobs.values(), key=lambda j: j.get("publicationDate") or "", reverse=True
    )[:30]

    return {"jobs": jobs_list, "keywords": keywords}
# --- Courses Routes ---
# --- Courses Routes ---

@app.get("/api/instructor/courses")
def get_courses(page: int = 0, size: int = 10, search: Optional[str] = None, instructorId: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(CourseDB)
    if instructorId:
        query = query.filter(CourseDB.instructorId == instructorId)
    if search:
        query = query.filter(CourseDB.title.ilike(f"%{search}%"))
    total_count = query.count()
    courses = query.order_by(CourseDB.createdAt.desc()).offset(page * size).limit(size).all()

    course_ids = [c.id for c in courses]
    modules = db.query(ModuleDB).filter(ModuleDB.courseId.in_(course_ids)).all() if course_ids else []

    return {
        "courses": [
            {
                "id": c.id,
                "title": c.title,
                "level": c.level,
                "language": c.language,
                "status": c.status,
                "totalLessons": c.totalLessons,
                "createdAt": c.createdAt.isoformat() if c.createdAt else None,
            }
            for c in courses
        ],
        "modules": [{"id": m.id, "courseId": m.courseId, "title": m.title} for m in modules],
        "totalCount": total_count,
    }


@app.get("/api/instructor/courses/{course_id}")
def get_course(course_id: str, db: Session = Depends(get_db)):
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    modules = db.query(ModuleDB).filter(ModuleDB.courseId == course_id).order_by(ModuleDB.position).all()
    result_modules = []
    for m in modules:
        lessons = db.query(LessonDB).filter(LessonDB.moduleId == m.id).order_by(LessonDB.order).all()
        result_modules.append({
            "moduleId": m.id,
            "title": m.title,
            "description": m.description,
            "order": m.position,
            "lessons": [
                {
                    "lessonId": l.id, "id": l.id, "title": l.title, "type": l.type,
                    "contentUrl": l.contentUrl, "content": l.content, "duration": l.duration,
                    "quiz": l.quiz, "assignment": l.assignment,
                } for l in lessons
            ],
        })
    return {
        "id": course.id, "title": course.title, "description": course.description,
        "shortDescription": course.shortDescription, "status": course.status,
        "level": course.level, "language": course.language, "thumbnail": course.thumbnail,
        "totalDurationMinutes": course.totalDurationMinutes, "pricingType": course.pricingType,
        "price": course.price, "discountPrice": course.discountPrice, "tags": course.tags or [],
        "_modules": result_modules,
    }


def _save_modules(db: Session, course_id: str, modules: List[ModulePayload]):
    for mod_index, mod in enumerate(modules):
        new_module = ModuleDB(courseId=course_id, title=mod.title, description=mod.description, position=mod.order or (mod_index + 1))
        db.add(new_module)
        db.flush()
        for les in mod.lessons:
            db.add(LessonDB(
                courseId=course_id, moduleId=new_module.id, title=les.title, type=les.type,
                contentUrl=les.contentUrl, content=les.content, duration=les.duration, order=les.order,
                quiz=les.quiz.model_dump() if les.quiz else None,
                assignment=les.assignment.model_dump() if les.assignment else None,
            ))


@app.post("/api/instructor/courses")
async def create_course(payload: CoursePayload, db: Session = Depends(get_db)):
    if not payload.instructorId:
        raise HTTPException(status_code=400, detail="instructorId is required.")
    try:
        instructor = await prisma_db.instructor.find_first(where={"id": payload.instructorId})
        is_company_course = bool(instructor and instructor.accountType == "company")
        new_course = CourseDB(
            instructorId=payload.instructorId,
            isCompanyCourse=is_company_course,
            title=payload.title, description=payload.description, shortDescription=payload.shortDescription,
            status=payload.status, level=payload.level, language=payload.language, thumbnail=payload.thumbnail,
            totalLessons=payload.totalLessons, totalDurationMinutes=payload.totalDurationMinutes,
            pricingType=payload.pricingType, price=payload.price, discountPrice=payload.discountPrice,
            tags=payload.tags,
        )
        db.add(new_course)
        db.flush()
        _save_modules(db, new_course.id, payload.modules)
 
        if is_company_course:
            company = db.query(CompanyDB).filter(CompanyDB.instructorId == payload.instructorId).first()
            if company:
                db.add(CompanyCourseDB(companyId=company.id, courseId=new_course.id, source="own"))
 
        db.commit()
        return {"message": "Success", "id": new_course.id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")
 
 


@app.put("/api/instructor/courses/{course_id}")
def update_course(course_id: str, payload: CoursePayload, db: Session = Depends(get_db)):
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        course.title = payload.title
        course.description = payload.description
        course.shortDescription = payload.shortDescription
        course.status = payload.status
        course.level = payload.level
        course.language = payload.language
        course.thumbnail = payload.thumbnail
        course.totalLessons = payload.totalLessons
        course.totalDurationMinutes = payload.totalDurationMinutes
        course.pricingType = payload.pricingType
        course.price = payload.price
        course.discountPrice = payload.discountPrice
        course.tags = payload.tags

        db.query(LessonDB).filter(LessonDB.courseId == course_id).delete()
        db.query(ModuleDB).filter(ModuleDB.courseId == course_id).delete()
        db.flush()
        _save_modules(db, course.id, payload.modules)

        db.commit()
        return {"message": "Success", "id": course.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")


@app.delete("/api/instructor/courses/{course_id}")
def delete_course(course_id: str, db: Session = Depends(get_db)):
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        db.query(LessonDB).filter(LessonDB.courseId == course_id).delete()
        db.query(ModuleDB).filter(ModuleDB.courseId == course_id).delete()
        db.delete(course)
        db.commit()
        return {"message": "Deleted"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")


@app.get("/api/instructor/courses/{course_id}/enriched")
def get_course_enriched(course_id: str, db: Session = Depends(get_db)):
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    modules = db.query(ModuleDB).filter(ModuleDB.courseId == course_id).order_by(ModuleDB.position).all()
    result_modules = []
    for m in modules:
        lessons = db.query(LessonDB).filter(LessonDB.moduleId == m.id).order_by(LessonDB.order).all()
        result_modules.append({
            "moduleId": m.id, "id": m.id, "title": m.title, "description": m.description,
            "order": m.position,
            "lessons": [
                {
                    "id": l.id, "lessonId": l.id, "title": l.title, "type": l.type,
                    "contentUrl": l.contentUrl, "content": l.content, "duration": l.duration,
                    "quiz": l.quiz, "assignment": l.assignment,
                } for l in lessons
            ],
        })

    course_data = {
        "id": course.id, "title": course.title, "description": course.description,
        "shortDescription": course.shortDescription, "status": course.status,
        "level": course.level, "language": course.language, "thumbnail": course.thumbnail,
        "totalLessons": course.totalLessons, "totalDurationMinutes": course.totalDurationMinutes,
        "pricingType": course.pricingType, "price": course.price, "discountPrice": course.discountPrice,
        "tags": course.tags or [],
        "createdAt": course.createdAt.isoformat() if course.createdAt else None,
    }

    return {"courseData": course_data, "modules": result_modules}



@app.get("/api/courses")
def get_all_courses(search: Optional[str] = None, studentId: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(CourseDB).filter(CourseDB.status == "PUBLISHED")
    if search:
        query = query.filter(CourseDB.title.ilike(f"%{search}%"))
    courses = query.order_by(CourseDB.createdAt.desc()).all()
 
    allowed_company_instructor_ids = set()
    if studentId:
        employee_company_ids = [
            row.companyId for row in db.query(CompanyEmployeeDB).filter(CompanyEmployeeDB.studentId == studentId).all()
        ]
        if employee_company_ids:
            companies = db.query(CompanyDB).filter(CompanyDB.id.in_(employee_company_ids)).all()
            allowed_company_instructor_ids = {c.instructorId for c in companies}
 
    visible_courses = [
        c for c in courses
        if not c.isCompanyCourse or c.instructorId in allowed_company_instructor_ids
    ]
 
    return [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "shortDescription": c.shortDescription,
            "thumbnail": c.thumbnail,
            "level": c.level,
            "language": c.language,
            "totalLessons": c.totalLessons,
            "totalDurationMinutes": c.totalDurationMinutes,
            "price": c.price,
            "pricingType": c.pricingType,
            "isCompanyCourse": c.isCompanyCourse,
        }
        for c in visible_courses
    ]
 


@app.get("/api/student/courses/{course_id}")
def get_student_course_detail(course_id: str, db: Session = Depends(get_db)):
    course = db.query(CourseDB).filter(CourseDB.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    return {
        "id": course.id,
        "title": course.title,
        "description": course.description,
        "shortDescription": course.shortDescription,
        "thumbnail": course.thumbnail,
        "status": course.status,
        "level": course.level,
        "language": course.language,
        "pricingType": course.pricingType,
        "price": course.price,
        "discountPrice": course.discountPrice,
        "totalLessons": course.totalLessons,
        "totalDurationMinutes": course.totalDurationMinutes,
        "tags": course.tags,
        "instructorId": course.instructorId,
        "createdAt": course.createdAt,
    }


@app.get("/api/student/courses/{course_id}/modules")
def get_student_course_modules(course_id: str, db: Session = Depends(get_db)):
    modules = (
        db.query(ModuleDB)
        .filter(ModuleDB.courseId == course_id)
        .order_by(ModuleDB.position.asc())
        .all()
    )

    result = []
    for module in modules:
        lessons = (
            db.query(LessonDB)
            .filter(LessonDB.moduleId == module.id)
            .order_by(LessonDB.order.asc())
            .all()
        )

        lesson_list = [
            {
                "lessonId": lesson.id,
                "id": lesson.id,
                "title": lesson.title,
                "type": lesson.type,
                "content": lesson.content,
                "contentUrl": lesson.contentUrl,
                "duration": lesson.duration,
                "order": lesson.order,
                "isCompleted": False,  # niche note dekho
                "quiz": lesson.quiz,
                "assignment": lesson.assignment,
            }
            for lesson in lessons
        ]

        result.append({
            "moduleId": module.id,
            "id": module.id,
            "title": module.title,
            "description": module.description,
            "position": module.position,
            "lessons": lesson_list,
        })

    return {"modules": result}

# =====================================================================
# Agar "/api/student/company-courses" route pehle se maujood nahi hai
# (ya kisi aur shape mein hai), to ye add/replace kar do - file ke end
# mein.
# =====================================================================

@app.get("/api/student/company-courses")
def get_student_company_courses(studentId: str, db: Session = Depends(get_db)):
    employee_links = db.query(CompanyEmployeeDB).filter(CompanyEmployeeDB.studentId == studentId).all()
    company_ids = list({e.companyId for e in employee_links})
    if not company_ids:
        return {"courses": []}

    companies = db.query(CompanyDB).filter(CompanyDB.id.in_(company_ids)).all()
    companies_by_id = {c.id: c for c in companies}

    company_course_links = (
        db.query(CompanyCourseDB).filter(CompanyCourseDB.companyId.in_(company_ids)).all()
    )
    course_ids = list({link.courseId for link in company_course_links})
    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all()
    courses_by_id = {c.id: c for c in courses}

    # Jin courses mein student already enrolled hai, unhe yahan se exclude karo
    # (wo "My Courses" wali normal list mein already dikh rahe honge)
    existing_enrollments = db.query(EnrollmentDB).filter(EnrollmentDB.studentId == studentId).all()
    enrolled_course_ids = {e.courseId for e in existing_enrollments}

    result = []
    seen = set()
    for link in company_course_links:
        if link.courseId in enrolled_course_ids or link.courseId in seen:
            continue
        course = courses_by_id.get(link.courseId)
        if not course:
            continue
        seen.add(link.courseId)
        company = companies_by_id.get(link.companyId)
        result.append(
            {
                "id": course.id,
                "title": course.title,
                "description": course.shortDescription or course.description,
                "thumbnail": course.thumbnail,
                "level": course.level,
                "totalLessons": course.totalLessons,
                "companyName": company.name if company else "Your Company",
            }
        )

    return {"courses": result}
    
class EnrollmentCreate(BaseModel):
    courseId: str
    studentId: str
    status: Optional[str] = "ACTIVE"
    completed: Optional[bool] = False
    progressPercentage: Optional[float] = 0
    completedLessons: Optional[int] = 0


class EnrollmentUpdate(BaseModel):
    status: Optional[str] = None
    completed: Optional[bool] = None
    progressPercentage: Optional[float] = None
    completedLessons: Optional[int] = None
    completedAt: Optional[str] = None


def serialize_enrollment(e):
    return {
        "id": e.id,
        "courseId": e.courseId,
        "studentId": e.studentId,
        "status": e.status,
        "completed": e.completed,
        "progressPercentage": e.progressPercentage,
        "completedLessons": e.completedLessons,
        "enrolledAt": e.enrolledAt,
        "completedAt": e.completedAt,
    }


@app.get("/api/student/enrollments")
def get_enrollment(courseId: str, studentId: str, db: Session = Depends(get_db)):
    enrollment = (
        db.query(EnrollmentDB)
        .filter(EnrollmentDB.courseId == courseId, EnrollmentDB.studentId == studentId)
        .first()
    )
    return {"enrollment": serialize_enrollment(enrollment) if enrollment else None}



@app.post("/api/student/enrollments")
def create_enrollment(payload: EnrollmentCreate, db: Session = Depends(get_db)):
    course = db.get(CourseDB, payload.courseId)
    if course and course.isCompanyCourse:
        is_employee = (
            db.query(CompanyEmployeeDB)
            .join(CompanyDB, CompanyDB.id == CompanyEmployeeDB.companyId)
            .filter(CompanyEmployeeDB.studentId == payload.studentId, CompanyDB.instructorId == course.instructorId)
            .first()
        )
        if not is_employee:
            raise HTTPException(
                status_code=403,
                detail="This is a company-restricted course. You must be invited by the company to enroll.",
            )
 
    existing = (
        db.query(EnrollmentDB)
        .filter(
            EnrollmentDB.courseId == payload.courseId,
            EnrollmentDB.studentId == payload.studentId,
        )
        .first()
    )
    if existing:
        return {"enrollment": serialize_enrollment(existing)}
 
    enrollment = EnrollmentDB(
        courseId=payload.courseId,
        studentId=payload.studentId,
        status=payload.status,
        completed=payload.completed,
        progressPercentage=payload.progressPercentage,
        completedLessons=payload.completedLessons,
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return {"enrollment": serialize_enrollment(enrollment)}





@app.put("/api/student/enrollments/{enrollment_id}")
def update_enrollment(enrollment_id: str, payload: EnrollmentUpdate, db: Session = Depends(get_db)):
    enrollment = db.query(EnrollmentDB).filter(EnrollmentDB.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(enrollment, key, value)

    db.commit()
    db.refresh(enrollment)
    return {"enrollment": serialize_enrollment(enrollment)}



class InvoiceCreate(BaseModel):
    studentId: str
    courseId: str
    enrollmentId: Optional[str] = None
    paymentId: Optional[str] = None
    invoiceType: Optional[str] = "course_enrollment"
    invoiceStatus: Optional[str] = "paid"
    invoiceMethod: Optional[str] = "free"
    invoiceGateway: Optional[str] = "system"
    transactionId: Optional[str] = None
    gatewayTransactionId: Optional[str] = None
    invoiceAmount: Optional[float] = 0
    taxAmount: Optional[float] = 0
    totalAmount: Optional[float] = 0
    discountApplied: Optional[float] = 0
    currencyType: Optional[str] = "usd"
    isSuccessful: Optional[bool] = True
    receiptUrl: Optional[str] = None


def serialize_invoice(i):
    return {
        "id": i.id,
        "studentId": i.studentId,
        "courseId": i.courseId,
        "enrollmentId": i.enrollmentId,
        "paymentId": i.paymentId,
        "invoiceType": i.invoiceType,
        "invoiceStatus": i.invoiceStatus,
        "invoiceMethod": i.invoiceMethod,
        "invoiceGateway": i.invoiceGateway,
        "transactionId": i.transactionId,
        "gatewayTransactionId": i.gatewayTransactionId,
        "invoiceAmount": i.invoiceAmount,
        "taxAmount": i.taxAmount,
        "totalAmount": i.totalAmount,
        "discountApplied": i.discountApplied,
        "currencyType": i.currencyType,
        "isSuccessful": i.isSuccessful,
        "receiptUrl": i.receiptUrl,
        "invoiceDate": i.invoiceDate,
        "invoiceCompletedAt": i.invoiceCompletedAt,
    }


@app.post("/api/student/invoices")
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db)):
    invoice = InvoiceDB(
        studentId=payload.studentId,
        courseId=payload.courseId,
        enrollmentId=payload.enrollmentId,
        paymentId=payload.paymentId,
        invoiceType=payload.invoiceType,
        invoiceStatus=payload.invoiceStatus,
        invoiceMethod=payload.invoiceMethod,
        invoiceGateway=payload.invoiceGateway,
        transactionId=payload.transactionId,
        gatewayTransactionId=payload.gatewayTransactionId,
        invoiceAmount=payload.invoiceAmount,
        taxAmount=payload.taxAmount,
        totalAmount=payload.totalAmount,
        discountApplied=payload.discountApplied,
        currencyType=payload.currencyType,
        isSuccessful=payload.isSuccessful,
        receiptUrl=payload.receiptUrl,
        invoiceCompletedAt=utc_now() if payload.isSuccessful else None,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return {"invoice": serialize_invoice(invoice)}


@app.get("/api/student/invoices")
def get_invoices(studentId: str, courseId: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(InvoiceDB).filter(InvoiceDB.studentId == studentId)
    if courseId:
        query = query.filter(InvoiceDB.courseId == courseId)
    invoices = query.order_by(InvoiceDB.invoiceDate.desc()).all()
    return {"invoices": [serialize_invoice(i) for i in invoices]}





class StripeCheckoutRequest(BaseModel):
    courseId: str
    studentId: str
    amount: float
    currency: Optional[str] = "usd"
    successUrl: str
    cancelUrl: str


@app.post("/api/student/stripe/checkout")
def create_stripe_checkout(payload: StripeCheckoutRequest, db: Session = Depends(get_db)):
    course = db.query(CourseDB).filter(CourseDB.id == payload.courseId).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": payload.currency,
                        "product_data": {"name": course.title},
                        "unit_amount": int(round(payload.amount * 100)),
                    },
                    "quantity": 1,
                }
            ],
            success_url=payload.successUrl,
            cancel_url=payload.cancelUrl,
            metadata={
                "courseId": payload.courseId,
                "studentId": payload.studentId,
            },
        )
        return {"url": session.url, "sessionId": session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


class StripeCompleteRequest(BaseModel):
    courseId: str
    studentId: str
    checkoutSessionId: str


@app.post("/api/student/stripe/complete")
def complete_stripe_payment(payload: StripeCompleteRequest, db: Session = Depends(get_db)):
    try:
        session = stripe.checkout.Session.retrieve(payload.checkoutSessionId)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not verify Stripe session: {str(e)}")

    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed yet.")

    enrollment = (
        db.query(EnrollmentDB)
        .filter(EnrollmentDB.courseId == payload.courseId, EnrollmentDB.studentId == payload.studentId)
        .first()
    )
    if not enrollment:
        enrollment = EnrollmentDB(
            courseId=payload.courseId,
            studentId=payload.studentId,
            status="ACTIVE",
            completed=False,
        )
        db.add(enrollment)
        db.commit()
        db.refresh(enrollment)

    existing_invoice = db.query(InvoiceDB).filter(InvoiceDB.transactionId == session.id).first()
    if existing_invoice:
        return {"enrollment": serialize_enrollment(enrollment), "invoice": serialize_invoice(existing_invoice)}

    amount_paid = (session.amount_total or 0) / 100

    invoice = InvoiceDB(
        studentId=payload.studentId,
        courseId=payload.courseId,
        enrollmentId=enrollment.id,
        paymentId=session.payment_intent,
        invoiceType="course_enrollment",
        invoiceStatus="paid",
        invoiceMethod="stripe",
        invoiceGateway="stripe",
        transactionId=session.id,
        gatewayTransactionId=session.payment_intent,
        invoiceAmount=amount_paid,
        taxAmount=0,
        totalAmount=amount_paid,
        discountApplied=0,
        currencyType=session.currency or "usd",
        isSuccessful=True,
        invoiceCompletedAt=utc_now(),
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    return {"enrollment": serialize_enrollment(enrollment), "invoice": serialize_invoice(invoice)}


@app.get("/api/student/my-courses")
def get_my_courses(studentId: str, db: Session = Depends(get_db)):
    enrollments = (
        db.query(EnrollmentDB)
        .filter(EnrollmentDB.studentId == studentId)
        .order_by(EnrollmentDB.enrolledAt.desc())
        .all()
    )

    course_ids = [e.courseId for e in enrollments]
    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all() if course_ids else []
    courses_by_id = {c.id: c for c in courses}

    result = []
    for enrollment in enrollments:
        course = courses_by_id.get(enrollment.courseId)
        if not course:
            continue

        total_lessons = course.totalLessons or 0
        completed_lessons = enrollment.completedLessons or 0
        completion_percentage = (
            round((completed_lessons / total_lessons) * 100) if total_lessons > 0 else 0
        )
        is_completed = bool(enrollment.completed) or (total_lessons > 0 and completed_lessons >= total_lessons)

        result.append({
            "id": course.id,
            "title": course.title,
            "description": course.shortDescription or course.description,
            "thumbnail": course.thumbnail,
            "level": course.level,
            "language": course.language,
            "status": "COMPLETED" if is_completed else "ACTIVE",
            "completionPercentage": completion_percentage,
            "enrolledAt": enrollment.enrolledAt,
            "totalLessons": total_lessons,
            "completedLessons": completed_lessons,
            "completed": is_completed,
        })

    return {"courses": result}

class LessonProgressCompleteRequest(BaseModel):
    enrollmentId: str
    studentId: str
    courseId: str
    lessonId: str


@app.post("/api/student/lesson-progress/complete")
def complete_lesson_progress(payload: LessonProgressCompleteRequest, db: Session = Depends(get_db)):
    progress = (
        db.query(LessonProgressDB)
        .filter(LessonProgressDB.studentId == payload.studentId, LessonProgressDB.lessonId == payload.lessonId)
        .first()
    )

    if progress:
        progress.status = "COMPLETED"
        progress.progressPercentage = 100
        progress.completedAt = utc_now()
    else:
        progress = LessonProgressDB(
            enrollmentId=payload.enrollmentId,
            studentId=payload.studentId,
            courseId=payload.courseId,
            lessonId=payload.lessonId,
            status="COMPLETED",
            progressPercentage=100,
            completedAt=utc_now(),
        )
        db.add(progress)

    db.commit()
    db.refresh(progress)

    enrollment = db.query(EnrollmentDB).filter(EnrollmentDB.id == payload.enrollmentId).first()
    if enrollment:
        completed_count = (
            db.query(LessonProgressDB)
            .filter(LessonProgressDB.enrollmentId == payload.enrollmentId, LessonProgressDB.status == "COMPLETED")
            .count()
        )
        enrollment.completedLessons = completed_count
        db.commit()

    return {
        "progress": {
            "id": progress.id,
            "lessonId": progress.lessonId,
            "status": progress.status,
            "progressPercentage": progress.progressPercentage,
            "completedAt": progress.completedAt,
        }
    }


@app.get("/api/student/lesson-progress")
def get_lesson_progress(courseId: str, studentId: str, db: Session = Depends(get_db)):
    records = (
        db.query(LessonProgressDB)
        .filter(LessonProgressDB.courseId == courseId, LessonProgressDB.studentId == studentId)
        .all()
    )
    return {
        "progress": [
            {
                "id": r.id,
                "lessonId": r.lessonId,
                "status": r.status,
                "progressPercentage": r.progressPercentage,
                "completedAt": r.completedAt,
            }
            for r in records
        ]
    }


class QuizSubmitRequest(BaseModel):
    lessonId: str
    studentId: str
    enrollmentId: str
    courseId: str
    answers: dict


@app.post("/api/student/quiz/submit")
def submit_quiz(payload: QuizSubmitRequest, db: Session = Depends(get_db)):
    lesson = db.query(LessonDB).filter(LessonDB.id == payload.lessonId).first()
    if not lesson or not lesson.quiz:
        raise HTTPException(status_code=404, detail="Quiz not found for this lesson")

    questions = lesson.quiz.get("questions", [])
    total_marks = sum(float(q.get("marks", 1)) for q in questions) or 1
    earned_marks = 0
    for question in questions:
        selected_option_id = payload.answers.get(question.get("id"))
        correct_option = next((o for o in question.get("options", []) if o.get("isCorrect")), None)
        if correct_option and selected_option_id == correct_option.get("id"):
            earned_marks += float(question.get("marks", 1))

    percentage = (earned_marks / total_marks) * 100
    passing_percentage = float(lesson.quiz.get("passingPercentage", 70))
    passed = percentage >= passing_percentage

    attempt = QuizAttemptDB(
        lessonId=payload.lessonId,
        studentId=payload.studentId,
        score=earned_marks,
        percentage=percentage,
        passed=passed,
        answers=payload.answers,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    if passed:
        complete_lesson_progress(
            LessonProgressCompleteRequest(
                enrollmentId=payload.enrollmentId,
                studentId=payload.studentId,
                courseId=payload.courseId,
                lessonId=payload.lessonId,
            ),
            db,
        )

    return {
        "attempt": {
            "id": attempt.id,
            "score": attempt.score,
            "percentage": attempt.percentage,
            "passed": attempt.passed,
        }
    }


class AssignmentSubmitRequest(BaseModel):
    lessonId: str
    studentId: str
    enrollmentId: str
    answerText: str


@app.post("/api/student/assignment/submit")
def submit_assignment(payload: AssignmentSubmitRequest, db: Session = Depends(get_db)):
    submission = AssignmentSubmissionDB(
        lessonId=payload.lessonId,
        enrollmentId=payload.enrollmentId,
        studentId=payload.studentId,
        answerText=payload.answerText,
        submissionText=payload.answerText,
        status="SUBMITTED",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    return {
        "submission": {
            "id": submission.id,
            "lessonId": submission.lessonId,
            "status": submission.status,
            "answerText": submission.answerText,
            "submittedAt": submission.submittedAt,
        }
    }


@app.get("/api/student/assignment-submissions")
def get_assignment_submissions(studentId: str, db: Session = Depends(get_db)):
    submissions = (
        db.query(AssignmentSubmissionDB)
        .filter(AssignmentSubmissionDB.studentId == studentId)
        .order_by(AssignmentSubmissionDB.submittedAt.desc())
        .all()
    )
    return {
        "submissions": [
            {
                "id": s.id,
                "lessonId": s.lessonId,
                "enrollmentId": s.enrollmentId,
                "status": s.status,
                "marks": s.marks,
                "feedback": s.feedback,
                "submittedAt": s.submittedAt,
                "gradedAt": s.gradedAt,
            }
            for s in submissions
        ]
    }





ROADMAP_GENERATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "roadmapTitle": {"type": "string"},
        "roadmapDescription": {"type": "string"},
        "difficultyLevel": {"type": "string", "enum": ["beginner", "intermediate", "advanced"]},
        "estimatedWeeks": {"type": "integer", "minimum": 1},
        "courseIds": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["roadmapTitle", "roadmapDescription", "difficultyLevel", "estimatedWeeks", "courseIds", "reasoning"],
}


async def ai_generate_roadmap(current_skills: str, goal: str, time_commitment: str, available_courses: List[dict]) -> dict:
    courses_text = "\n".join(
        f"- id: {c['id']}, title: {c['title']}, level: {c['level']}, "
        f"duration: {c['totalDurationMinutes']} min, description: {c['shortDescription'] or ''}"
        for c in available_courses
    )
    prompt = f"""
A student wants a personalized learning roadmap.

What they already know: {current_skills}
Their goal: {goal}
Time they can commit per week: {time_commitment}

Available courses in the catalog:
{courses_text}

Pick the most relevant courses from the catalog above, in the best learning order, to help this
student reach their goal efficiently. Only use course ids from the list above, never invent new
ones. Skip courses that don't fit. Order matters - foundational courses should come before advanced
ones.
""".strip()
    return await structured_response(prompt, "roadmap_generation", ROADMAP_GENERATION_SCHEMA, temperature=0.3)




def serialize_roadmap(r):
    return {
        "id": r.id,
        "studentId": r.studentId,
        "roadmapTitle": r.roadmapTitle,
        "roadmapDescription": r.roadmapDescription,
        "roadmapStatus": r.roadmapStatus,
        "totalCourse": r.totalCourse,
        "difficultyLevel": r.difficultyLevel,
        "estimatedDuration": r.estimatedDuration,
        "courseSequence": r.courseSequence or [],
        "aiReasoning": r.aiReasoning,
        "isAiGenerated": r.isAiGenerated,
        "createdAt": r.createdAt,
    }


@app.get("/api/student/roadmaps")
def list_roadmaps(studentId: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(RoadmapDB)
    if studentId:
        query = query.filter((RoadmapDB.studentId.is_(None)) | (RoadmapDB.studentId == studentId))
    else:
        query = query.filter(RoadmapDB.studentId.is_(None))
    roadmaps = query.order_by(RoadmapDB.createdAt.desc()).all()
    return {"roadmaps": [serialize_roadmap(r) for r in roadmaps]}


@app.get("/api/student/roadmaps/{roadmap_id}")
def get_roadmap_detail(roadmap_id: str, db: Session = Depends(get_db)):
    roadmap = db.query(RoadmapDB).filter(RoadmapDB.id == roadmap_id).first()
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")

    course_ids = roadmap.courseSequence or []
    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all() if course_ids else []
    courses_by_id = {c.id: c for c in courses}

    ordered_courses = [
        {
            "id": courses_by_id[cid].id,
            "title": courses_by_id[cid].title,
            "shortDescription": courses_by_id[cid].shortDescription,
            "thumbnail": courses_by_id[cid].thumbnail,
            "level": courses_by_id[cid].level,
            "totalDurationMinutes": courses_by_id[cid].totalDurationMinutes,
            "totalLessons": courses_by_id[cid].totalLessons,
        }
        for cid in course_ids
        if cid in courses_by_id
    ]

    data = serialize_roadmap(roadmap)
    data["courses"] = ordered_courses
    return {"roadmap": data}


class RoadmapGenerateRequest(BaseModel):
    studentId: str
    currentSkills: str
    goal: str
    timeCommitment: str


@app.post("/api/student/roadmaps/generate")
async def generate_roadmap(payload: RoadmapGenerateRequest, db: Session = Depends(get_db)):
    courses = db.query(CourseDB).filter(CourseDB.status == "PUBLISHED").all()
    if not courses:
        raise HTTPException(status_code=400, detail="No published courses available to build a roadmap.")

    available_courses = [
        {
            "id": c.id,
            "title": c.title,
            "level": c.level,
            "totalDurationMinutes": c.totalDurationMinutes,
            "shortDescription": c.shortDescription,
        }
        for c in courses
    ]

    ai_result = await ai_generate_roadmap(
        payload.currentSkills, payload.goal, payload.timeCommitment, available_courses
    )

    valid_course_ids = {c["id"] for c in available_courses}
    course_sequence = [cid for cid in ai_result.get("courseIds", []) if cid in valid_course_ids]

    if not course_sequence:
        raise HTTPException(status_code=500, detail="AI could not match any courses to this goal.")

    roadmap = RoadmapDB(
        studentId=payload.studentId,
        roadmapTitle=ai_result.get("roadmapTitle", "Your Personalized Roadmap"),
        roadmapDescription=ai_result.get("roadmapDescription"),
        roadmapStatus="ACTIVE",
        totalCourse=len(course_sequence),
        difficultyLevel=ai_result.get("difficultyLevel"),
        estimatedDuration=ai_result.get("estimatedWeeks"),
        courseSequence=course_sequence,
        currentSkillsInput=payload.currentSkills,
        goalInput=payload.goal,
        timeCommitmentInput=payload.timeCommitment,
        aiReasoning=ai_result.get("reasoning"),
        isAiGenerated=True,
    )
    db.add(roadmap)
    db.commit()
    db.refresh(roadmap)

    return {"roadmap": serialize_roadmap(roadmap)}


@app.post("/api/sessions/{session_id}/answer-audio")
async def submit_audio_answer(
    session_id: str,
    question_id: str,
    file: UploadFile = File(...),
    duration_seconds: Optional[float] = Form(None),
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
    return await process_transcript_answer(db, session, question, transcript, str(audio_path), duration_seconds)





@app.get("/api/student/assistant/chat")
def get_assistant_chat_history(studentId: str, db: Session = Depends(get_db)):
    messages = (
        db.query(CourseChatMessageDB)
        .filter(CourseChatMessageDB.studentId == studentId)
        .order_by(CourseChatMessageDB.createdAt.asc())
        .all()
    )
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "citedLessons": m.citedLessons or [],
                "grounded": m.grounded,
                "createdAt": m.createdAt,
            }
            for m in messages
        ]
    }


class AssistantChatRequest(BaseModel):
    studentId: str
    message: str = Field(..., min_length=1)


@app.post("/api/student/assistant/chat")
async def post_assistant_chat_message(payload: AssistantChatRequest, db: Session = Depends(get_db)):
    enrollments = db.query(EnrollmentDB).filter(EnrollmentDB.studentId == payload.studentId).all()
    course_ids = [e.courseId for e in enrollments]
    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all() if course_ids else []
    courses_by_id = {c.id: c for c in courses}

    enrollments_summary = []
    for e in enrollments:
        course = courses_by_id.get(e.courseId)
        if not course:
            continue
        total_lessons = course.totalLessons or 0
        completed_lessons = e.completedLessons or 0
        completion_percentage = round((completed_lessons / total_lessons) * 100) if total_lessons > 0 else 0
        enrollments_summary.append({
            "title": course.title,
            "totalLessons": total_lessons,
            "completedLessons": completed_lessons,
            "completionPercentage": completion_percentage,
        })

    lessons_with_course = []
    if course_ids:
        all_lessons = db.query(LessonDB).filter(LessonDB.courseId.in_(course_ids)).all()
        for lesson in all_lessons:
            course = courses_by_id.get(lesson.courseId)
            if course:
                lessons_with_course.append((lesson, course))

    history = (
        db.query(CourseChatMessageDB)
        .filter(CourseChatMessageDB.studentId == payload.studentId)
        .order_by(CourseChatMessageDB.createdAt.asc())
        .all()
    )

    retrieved = retrieve_relevant_lessons_global(lessons_with_course, payload.message, limit=5)
    ai_result = await ai_student_assistant_answer(enrollments_summary, retrieved, history, payload.message)

    retrieved_lookup = {(course.title, lesson.title): lesson.id for lesson, course in retrieved}
    cited = []
    for item in ai_result.get("cited_lessons", []):
        key = (item.get("course_title"), item.get("lesson_title"))
        lesson_id = retrieved_lookup.get(key)
        if lesson_id:
            cited.append({
                "lessonId": lesson_id,
                "lessonTitle": item.get("lesson_title"),
                "courseTitle": item.get("course_title"),
            })

    user_msg = CourseChatMessageDB(studentId=payload.studentId, role="user", content=payload.message)
    assistant_msg = CourseChatMessageDB(
        studentId=payload.studentId,
        role="assistant",
        content=ai_result.get("answer", ""),
        citedLessons=cited,
        grounded=ai_result.get("grounded", False),
    )
    db.add(user_msg)
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return {
        "message": {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "citedLessons": assistant_msg.citedLessons or [],
            "grounded": assistant_msg.grounded,
            "createdAt": assistant_msg.createdAt,
        }
    }





class ResumeGenerateRequest(BaseModel):
    studentId: str
    mode: Literal["ai_generate", "custom_text", "format_reference"]
    prompt: Optional[str] = None
    rawText: Optional[str] = None
    formatReferenceText: Optional[str] = None
    studentProfile: Optional[dict] = None
    templateId: Optional[str] = "modern"


@app.post("/api/student/resume/generate")
async def generate_resume(payload: ResumeGenerateRequest, db: Session = Depends(get_db)):
    if payload.mode == "ai_generate":
        if not payload.prompt:
            raise HTTPException(status_code=400, detail="Prompt is required for AI generation mode.")
        resume_data = await ai_generate_resume_from_profile(payload.studentProfile or {}, payload.prompt)
        input_text = payload.prompt
    elif payload.mode == "custom_text":
        if not payload.rawText:
            raise HTTPException(status_code=400, detail="Please paste your resume content or format.")
        resume_data = await ai_generate_resume_from_custom_text(payload.rawText, payload.studentProfile)
        input_text = payload.rawText
    else:
        if not payload.formatReferenceText:
            raise HTTPException(status_code=400, detail="Please upload a reference resume first.")
        resume_data = await ai_generate_resume_with_format_reference(
            payload.studentProfile or {}, payload.formatReferenceText, payload.prompt
        )
        input_text = payload.formatReferenceText

    record = ResumeDB(
        studentId=payload.studentId,
        templateId=payload.templateId or "modern",
        sourceMode=payload.mode,
        inputText=input_text,
        resumeData=resume_data,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {"resume": resume_data, "resumeId": record.id, "templateId": record.templateId}


@app.post("/api/student/resume/extract-pdf")
async def extract_resume_pdf(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    try:
        contents = await file.read()
        reader = PdfReader(io.BytesIO(contents))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        extracted_text = "\n".join(text_parts).strip()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read this PDF: {exc}")
    if not extracted_text:
        raise HTTPException(
            status_code=400,
            detail="Could not extract text from this PDF. It might be a scanned image rather than text.",
        )
    return {"extractedText": extracted_text}


@app.get("/api/instructor/enrollments-overview")
def get_instructor_enrollments_overview(
    instructorId: Optional[str] = None, search: Optional[str] = None, db: Session = Depends(get_db)
):
    query = db.query(CourseDB)
    if instructorId:
        query = query.filter(CourseDB.instructorId == instructorId)
    if search:
        query = query.filter(CourseDB.title.ilike(f"%{search}%"))
    courses = query.order_by(CourseDB.createdAt.desc()).all()

    course_ids = [c.id for c in courses]
    enrollments = db.query(EnrollmentDB).filter(EnrollmentDB.courseId.in_(course_ids)).all() if course_ids else []
    enrollment_counts: Dict[str, int] = {}
    for e in enrollments:
        enrollment_counts[e.courseId] = enrollment_counts.get(e.courseId, 0) + 1

    return {
        "courses": [
            {
                "id": c.id,
                "title": c.title,
                "status": c.status,
                "totalEnrollments": enrollment_counts.get(c.id, 0),
                "createdAt": c.createdAt,
            }
            for c in courses
        ],
        "totalCount": len(courses),
    }


@app.get("/api/instructor/courses/{course_id}/enrollments")
async def get_course_enrollments_for_instructor(course_id: str, db: Session = Depends(get_db)):
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    enrollments = (
        db.query(EnrollmentDB)
        .filter(EnrollmentDB.courseId == course_id)
        .order_by(EnrollmentDB.enrolledAt.desc())
        .all()
    )
    student_ids = list({e.studentId for e in enrollments})

    students_by_id = {}
    if student_ids:
        students = await prisma_db.student.find_many(where={"id": {"in": student_ids}})
        for s in students:
            students_by_id[s.id] = s

    progress_records = (
        db.query(LessonProgressDB).filter(LessonProgressDB.courseId == course_id).all() if student_ids else []
    )
    completed_by_student: Dict[str, int] = {}
    for p in progress_records:
        if p.status == "COMPLETED":
            completed_by_student[p.studentId] = completed_by_student.get(p.studentId, 0) + 1

    total_lessons = course.totalLessons or 0

    result = []
    for e in enrollments:
        student = students_by_id.get(e.studentId)
        completed_count = completed_by_student.get(e.studentId, 0)
        progress_pct = round((completed_count / total_lessons) * 100) if total_lessons > 0 else 0
        result.append({
            "enrollmentId": e.id,
            "studentId": e.studentId,
            "studentName": f"{student.firstName or ''} {student.lastName or ''}".strip() if student else "N/A",
            "studentEmail": student.email if student else None,
            "status": e.status,
            "completed": e.completed,
            "progressPercentage": progress_pct,
            "completedLessons": completed_count,
            "totalLessons": total_lessons,
            "enrolledAt": e.enrolledAt,
        })

    return {"course": {"id": course.id, "title": course.title, "totalLessons": total_lessons}, "enrollments": result}


@app.get("/api/instructor/courses/{course_id}/students/{student_id}/detail")
async def get_instructor_enrollment_detail(course_id: str, student_id: str, db: Session = Depends(get_db)):
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    student = await prisma_db.student.find_first(where={"id": student_id})

    enrollment = (
        db.query(EnrollmentDB)
        .filter(EnrollmentDB.courseId == course_id, EnrollmentDB.studentId == student_id)
        .first()
    )

    lessons = db.query(LessonDB).filter(LessonDB.courseId == course_id).order_by(LessonDB.order).all()
    lesson_ids = {l.id for l in lessons}

    progress_records = (
        db.query(LessonProgressDB)
        .filter(LessonProgressDB.courseId == course_id, LessonProgressDB.studentId == student_id)
        .all()
    )
    progress_by_lesson = {p.lessonId: p for p in progress_records}

    submissions = db.query(AssignmentSubmissionDB).filter(AssignmentSubmissionDB.studentId == student_id).all()
    submissions_by_lesson = {s.lessonId: s for s in submissions if s.lessonId in lesson_ids}

    lesson_list = []
    for lesson in lessons:
        progress = progress_by_lesson.get(lesson.id)
        submission = submissions_by_lesson.get(lesson.id)
        lesson_list.append({
            "lessonId": lesson.id,
            "title": lesson.title,
            "type": lesson.type,
            "status": progress.status if progress else "NOT_STARTED",
            "completedAt": progress.completedAt if progress else None,
            "assignment": lesson.assignment,
            "submission": (
                {
                    "id": submission.id,
                    "answerText": submission.answerText or submission.submissionText,
                    "status": submission.status,
                    "marks": submission.marks,
                    "feedback": submission.feedback,
                    "submittedAt": submission.submittedAt,
                    "gradedAt": submission.gradedAt,
                }
                if submission
                else None
            ),
        })

    completed_count = sum(1 for p in progress_records if p.status == "COMPLETED")
    total_lessons = course.totalLessons or 0
    progress_pct = round((completed_count / total_lessons) * 100) if total_lessons > 0 else 0

    return {
        "course": {"id": course.id, "title": course.title, "totalLessons": total_lessons},
        "student": (
            {"id": student.id, "name": f"{student.firstName or ''} {student.lastName or ''}".strip(), "email": student.email}
            if student
            else {"id": student_id, "name": "Student", "email": None}
        ),
        "enrollment": (
            {
                "id": enrollment.id,
                "status": enrollment.status,
                "enrolledAt": enrollment.enrolledAt,
                "completedLessons": completed_count,
                "totalLessons": total_lessons,
                "progressPercentage": progress_pct,
            }
            if enrollment
            else None
        ),
        "lessons": lesson_list,
    }


class GradeSubmissionRequest(BaseModel):
    marks: float
    feedback: Optional[str] = None


@app.post("/api/instructor/assignment-submissions/{submission_id}/grade")
def grade_assignment_submission(submission_id: str, payload: GradeSubmissionRequest, db: Session = Depends(get_db)):
    submission = db.get(AssignmentSubmissionDB, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    submission.marks = payload.marks
    submission.feedback = payload.feedback
    submission.status = "GRADED"
    submission.gradedAt = utc_now()
    db.commit()
    db.refresh(submission)

    return {
        "submission": {
            "id": submission.id,
            "marks": submission.marks,
            "feedback": submission.feedback,
            "status": submission.status,
            "gradedAt": submission.gradedAt,
        }
    }



def serialize_company(c):
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "instructorId": c.instructorId,
        "invitationLimit": c.invitationLimit,
    }
 
 
class CompanyRegisterRequest(BaseModel):
    instructorId: str
    name: str
    description: Optional[str] = ""
    invitationLimit: Optional[int] = 0
 

@app.post("/api/instructor/company")
def register_company(payload: CompanyRegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(CompanyDB).filter(CompanyDB.instructorId == payload.instructorId).first()
    if existing:
        raise HTTPException(status_code=400, detail="You have already registered a company.")
    company = CompanyDB(
        instructorId=payload.instructorId,
        name=payload.name,
        description=payload.description,
        planTier="free",
        invitationLimit=COMPANY_PLAN_LIMITS["free"],
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return {"company": serialize_company(company)}


 
class InviteEmployeeRequest(BaseModel):
    email: str
 
 
@app.post("/api/instructor/company/{company_id}/invite")
async def invite_employee(company_id: str, payload: InviteEmployeeRequest, db: Session = Depends(get_db)):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
 
    if company.invitationLimit and company.invitationLimit > 0:
        current_count = db.query(CompanyEmployeeDB).filter(CompanyEmployeeDB.companyId == company_id).count()
        if current_count >= company.invitationLimit:
            raise HTTPException(status_code=403, detail="Invitation limit reached for your subscription plan.")
 
    student = await prisma_db.student.find_first(where={"email": payload.email})
    if not student:
        raise HTTPException(
            status_code=404,
            detail="No student account found with this email. They must register on the platform first.",
        )
 
    existing = (
        db.query(CompanyEmployeeDB)
        .filter(CompanyEmployeeDB.companyId == company_id, CompanyEmployeeDB.studentId == student.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="This student is already invited to your company.")
 
    employee = CompanyEmployeeDB(companyId=company_id, studentId=student.id, email=payload.email, status="ACTIVE")
    db.add(employee)
    db.commit()
    db.refresh(employee)
 
    return {
        "employee": {
            "id": employee.id,
            "studentId": employee.studentId,
            "email": employee.email,
            "name": f"{student.firstName or ''} {student.lastName or ''}".strip(),
            "status": employee.status,
            "invitedAt": employee.invitedAt,
        }
    }
 
 
@app.get("/api/instructor/company/{company_id}/employees")
async def list_company_employees(company_id: str, db: Session = Depends(get_db)):
    employees = (
        db.query(CompanyEmployeeDB)
        .filter(CompanyEmployeeDB.companyId == company_id)
        .order_by(CompanyEmployeeDB.invitedAt.desc())
        .all()
    )
    student_ids = [e.studentId for e in employees]
    students_by_id = {}
    if student_ids:
        students = await prisma_db.student.find_many(where={"id": {"in": student_ids}})
        for s in students:
            students_by_id[s.id] = s
 
    return {
        "employees": [
            {
                "id": e.id,
                "studentId": e.studentId,
                "email": e.email,
                "name": (
                    f"{students_by_id[e.studentId].firstName or ''} {students_by_id[e.studentId].lastName or ''}".strip()
                    if e.studentId in students_by_id
                    else e.email
                ),
                "status": e.status,
                "invitedAt": e.invitedAt,
            }
            for e in employees
        ]
    }
 
 
# --- Company courses: khud banaye huye + catalog se select kiye huye ---
@app.post("/api/instructor/company/{company_id}/courses/{course_id}")
def add_course_to_company(company_id: str, course_id: str, db: Session = Depends(get_db)):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != "PUBLISHED":
        raise HTTPException(status_code=400, detail="Only published courses can be added.")
 
    is_paid = course.pricingType != "FREE" and (course.price or 0) > 0
    is_own = course.instructorId == company.instructorId
    if is_paid and not is_own:
        raise HTTPException(
            status_code=402,
            detail="This is a paid course. Please complete checkout to add it to your company.",
        )
 
    existing = (
        db.query(CompanyCourseDB)
        .filter(CompanyCourseDB.companyId == company_id, CompanyCourseDB.courseId == course_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="This course is already part of your company's offering.")
 
    source = "own" if is_own else "selected"
    link = CompanyCourseDB(companyId=company_id, courseId=course_id, source=source)
    db.add(link)
    db.commit()
    return {"message": "Course added to company offering."}
 
 
 
@app.delete("/api/instructor/company/{company_id}/courses/{course_id}")
def remove_course_from_company(company_id: str, course_id: str, db: Session = Depends(get_db)):
    link = (
        db.query(CompanyCourseDB)
        .filter(CompanyCourseDB.companyId == company_id, CompanyCourseDB.courseId == course_id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="This course is not part of your company's offering.")
    db.delete(link)
    db.commit()
    return {"message": "Removed."}
 
 
@app.get("/api/instructor/company/{company_id}/courses")
def list_company_courses(company_id: str, db: Session = Depends(get_db)):
    links = db.query(CompanyCourseDB).filter(CompanyCourseDB.companyId == company_id).all()
    course_ids = [link.courseId for link in links]
    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all() if course_ids else []
    courses_by_id = {c.id: c for c in courses}
    return {
        "courses": [
            {
                "id": link.courseId,
                "title": courses_by_id[link.courseId].title if link.courseId in courses_by_id else "Unknown",
                "source": link.source,
                "addedAt": link.addedAt,
            }
            for link in links
            if link.courseId in courses_by_id
        ]
    }
 
 
# --- Student-facing: company ke through assigned courses ---
 
@app.get("/api/student/company-courses")
def get_student_company_courses(studentId: str, db: Session = Depends(get_db)):
    employee_rows = db.query(CompanyEmployeeDB).filter(CompanyEmployeeDB.studentId == studentId).all()
    company_ids = [row.companyId for row in employee_rows]
    if not company_ids:
        return {"courses": []}
 
    links = db.query(CompanyCourseDB).filter(CompanyCourseDB.companyId.in_(company_ids)).all()
    course_ids = list({link.courseId for link in links})
    courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all() if course_ids else []
 
    return {
        "courses": [
            {
                "id": c.id,
                "title": c.title,
                "shortDescription": c.shortDescription,
                "thumbnail": c.thumbnail,
                "level": c.level,
                "totalLessons": c.totalLessons,
                "totalDurationMinutes": c.totalDurationMinutes,
            }
            for c in courses
        ]
    }
 



@app.post("/api/instructor/company/{company_id}/invite")
async def invite_employee(company_id: str, payload: InviteEmployeeRequest, db: Session = Depends(get_db)):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
 
    if company.invitationLimit and company.invitationLimit > 0:
        current_count = db.query(CompanyEmployeeDB).filter(CompanyEmployeeDB.companyId == company_id).count()
        if current_count >= company.invitationLimit:
            raise HTTPException(status_code=403, detail="Invitation limit reached for your subscription plan.")
 
    student = await prisma_db.student.find_first(where={"email": payload.email})
    if not student:
        raise HTTPException(
            status_code=404,
            detail="No student account found with this email. They must register on the platform first.",
        )
 
    existing = (
        db.query(CompanyEmployeeDB)
        .filter(CompanyEmployeeDB.companyId == company_id, CompanyEmployeeDB.studentId == student.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="This student is already invited to your company.")
 
    employee = CompanyEmployeeDB(companyId=company_id, studentId=student.id, email=payload.email, status="ACTIVE")
    db.add(employee)
    db.commit()
    db.refresh(employee)
 
    student_name = f"{student.firstName or ''} {student.lastName or ''}".strip()
    await send_invite_email(payload.email, company.name, student_name)
 
    return {
        "employee": {
            "id": employee.id,
            "studentId": employee.studentId,
            "email": employee.email,
            "name": student_name,
            "status": employee.status,
            "invitedAt": employee.invitedAt,
        }
    }
 



class CompanyUpgradeCheckoutRequest(BaseModel):
    planTier: str
    successUrl: str
    cancelUrl: str
 
 
@app.post("/api/instructor/company/{company_id}/upgrade/checkout")
def create_company_upgrade_checkout(company_id: str, payload: CompanyUpgradeCheckoutRequest, db: Session = Depends(get_db)):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if payload.planTier not in COMPANY_PLAN_PRICES_CENTS:
        raise HTTPException(status_code=400, detail="Invalid plan selected.")
 
    amount_cents = COMPANY_PLAN_PRICES_CENTS[payload.planTier]
    plan_label = COMPANY_PLAN_LABELS[payload.planTier]
 
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": f"SkillShift LMS - {plan_label} Plan (Company)"},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            success_url=payload.successUrl,
            cancel_url=payload.cancelUrl,
            metadata={"companyId": company_id, "planTier": payload.planTier},
        )
        return {"url": session.url, "sessionId": session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")
 
 
class CompanyUpgradeCompleteRequest(BaseModel):
    checkoutSessionId: str
 
 
@app.post("/api/instructor/company/{company_id}/upgrade/complete")
def complete_company_upgrade(company_id: str, payload: CompanyUpgradeCompleteRequest, db: Session = Depends(get_db)):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
 
    try:
        session = stripe.checkout.Session.retrieve(payload.checkoutSessionId)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not verify Stripe session: {str(e)}")
 
    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed yet.")
 
    plan_tier = session.metadata.get("planTier") if session.metadata else None
    if plan_tier not in COMPANY_PLAN_LIMITS:
        raise HTTPException(status_code=400, detail="Could not determine plan from this session.")
 
    company.planTier = plan_tier
    company.invitationLimit = COMPANY_PLAN_LIMITS[plan_tier]
    payment = PaymentDB(
        payerType="company",
        payerId=company.id,
        payerName=company.name,
        paymentType="company_upgrade",
        referenceTitle=f"{plan_tier.capitalize()} Plan",
        amount=PLAN_PRICES.get(plan_tier, 0),  
        stripeSessionId=payload.checkoutSessionId,
  )
    db.add(payment)
    db.commit()
    db.refresh(company)
 
    return {"company": serialize_company(company)}

@app.get("/api/instructor/company")
def get_company(instructorId: str, db: Session = Depends(get_db)):
    company = db.query(CompanyDB).filter(CompanyDB.instructorId == instructorId).first()
    if not company:
        return {"company": None}
    return {"company": serialize_company(company)}





class CompanyCourseCheckoutRequest(BaseModel):
    successUrl: str
    cancelUrl: str
 
 
@app.post("/api/instructor/company/{company_id}/courses/{course_id}/checkout")
def create_company_course_checkout(
    company_id: str, course_id: str, payload: CompanyCourseCheckoutRequest, db: Session = Depends(get_db)
):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != "PUBLISHED":
        raise HTTPException(status_code=400, detail="Only published courses can be added.")
    if course.pricingType == "FREE" or (course.price or 0) <= 0:
        raise HTTPException(status_code=400, detail="This course is free - add it directly without checkout.")
 
    existing = (
        db.query(CompanyCourseDB)
        .filter(CompanyCourseDB.companyId == company_id, CompanyCourseDB.courseId == course_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="This course is already part of your company's offering.")
 
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": f"{course.title} (Company License)"},
                        "unit_amount": int(round(course.price * 100)),
                    },
                    "quantity": 1,
                }
            ],
            success_url=payload.successUrl,
            cancel_url=payload.cancelUrl,
            metadata={"companyId": company_id, "courseId": course_id},
        )
        return {"url": session.url, "sessionId": session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")
 
 
class CompanyCourseCompleteRequest(BaseModel):
    checkoutSessionId: str
 
 
@app.post("/api/instructor/company/{company_id}/courses/{course_id}/complete")
def complete_company_course_checkout(
    company_id: str, course_id: str, payload: CompanyCourseCompleteRequest, db: Session = Depends(get_db)
):
    company = db.get(CompanyDB, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    course = db.get(CourseDB, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
 
    try:
        session = stripe.checkout.Session.retrieve(payload.checkoutSessionId)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not verify Stripe session: {str(e)}")
 
    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed yet.")
 
    existing = (
        db.query(CompanyCourseDB)
        .filter(CompanyCourseDB.companyId == company_id, CompanyCourseDB.courseId == course_id)
        .first()
    )
    if existing:
        return {"message": "Already added."}
 
    link = CompanyCourseDB(companyId=company_id, courseId=course_id, source="selected")
    payment = PaymentDB(                                  
       payerType="company",
       payerId=company_id,
       payerName=company.name,
       paymentType="company_course",
       referenceTitle=course.title,
       amount=course.price or 0,
       stripeSessionId=payload.checkoutSessionId,
    )
    db.add(link)
    db.commit()
    return {"message": "Course added to company offering."}




# =====================================================================
# Ye 3 naye routes file ke end mein (ya company routes ke section mein)
# add kar do. Enrollments ke liye nayi route ki zaroorat nahi -
# pehle se maujood "/api/instructor/enrollments-overview" (bina
# instructorId pass kiye) sab courses + enrollment counts deta hai,
# wahi admin page use karega.
# =====================================================================

@app.get("/api/admin/users")
async def list_admin_users(search: Optional[str] = None, page: int = 0, size: int = 10):
    where = {}
    if search:
        where = {
            "OR": [
                {"firstName": {"contains": search, "mode": "insensitive"}},
                {"lastName": {"contains": search, "mode": "insensitive"}},
                {"email": {"contains": search, "mode": "insensitive"}},
            ]
        }
    try:
        admins = await prisma_db.admin.find_many(
            where=where, skip=page * size, take=size, order={"createdAt": "desc"}
        )
        total = await prisma_db.admin.count(where=where)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

    return {
        "admins": [
            {
                "id": a.id,
                "firstName": a.firstName,
                "lastName": a.lastName,
                "email": a.email,
                "phoneNumber": getattr(a, "phoneNumber", None),
                "accountStatus": getattr(a, "accountStatus", None),
                "createdAt": a.createdAt,
            }
            for a in admins
        ],
        "totalCount": total,
    }


@app.get("/api/admin/instructors")
async def list_admin_instructors(search: Optional[str] = None, page: int = 0, size: int = 10):
    where = {}
    if search:
        where = {
            "OR": [
                {"firstName": {"contains": search, "mode": "insensitive"}},
                {"lastName": {"contains": search, "mode": "insensitive"}},
                {"email": {"contains": search, "mode": "insensitive"}},
            ]
        }
    try:
        instructors = await prisma_db.instructor.find_many(
            where=where, skip=page * size, take=size, order={"createdAt": "desc"}
        )
        total = await prisma_db.instructor.count(where=where)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

    return {
        "instructors": [
            {
                "id": i.id,
                "firstName": i.firstName,
                "lastName": i.lastName,
                "email": i.email,
                "accountType": getattr(i, "accountType", None),
                "accountStatus": getattr(i, "accountStatus", None),
                "createdAt": i.createdAt,
            }
            for i in instructors
        ],
        "totalCount": total,
    }


@app.get("/api/admin/courses")
async def list_admin_courses(
    search: Optional[str] = None, page: int = 0, size: int = 10, db: Session = Depends(get_db)
):
    query = db.query(CourseDB)
    if search:
        query = query.filter(CourseDB.title.ilike(f"%{search}%"))
    total = query.count()
    courses = query.order_by(CourseDB.createdAt.desc()).offset(page * size).limit(size).all()

    instructor_ids = list({c.instructorId for c in courses})
    instructors_by_id = {}
    if instructor_ids:
        try:
            instructors = await prisma_db.instructor.find_many(where={"id": {"in": instructor_ids}})
            for ins in instructors:
                instructors_by_id[ins.id] = f"{ins.firstName or ''} {ins.lastName or ''}".strip()
        except Exception:
            pass

    return {
        "courses": [
            {
                "id": c.id,
                "title": c.title,
                "instructorName": instructors_by_id.get(c.instructorId, "Unknown"),
                "level": c.level,
                "status": c.status,
                "createdAt": c.createdAt.isoformat() if c.createdAt else None,
            }
            for c in courses
        ],
        "totalCount": total,
    }



# =====================================================================
# Ye sab routes file ke end mein (company routes ke baad) add kar do
# =====================================================================

@app.get("/api/admin/students")
async def list_admin_students(search: Optional[str] = None, page: int = 0, size: int = 10):
    where = {}
    if search:
        where = {
            "OR": [
                {"firstName": {"contains": search, "mode": "insensitive"}},
                {"lastName": {"contains": search, "mode": "insensitive"}},
                {"email": {"contains": search, "mode": "insensitive"}},
            ]
        }
    try:
        students = await prisma_db.student.find_many(
            where=where, skip=page * size, take=size, order={"createdAt": "desc"}
        )
        total = await prisma_db.student.count(where=where)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

    return {
        "students": [
            {
                "id": s.id,
                "firstName": s.firstName,
                "lastName": s.lastName,
                "email": s.email,
                "phoneNumber": getattr(s, "phoneNumber", None),
                "accountStatus": getattr(s, "accountStatus", None),
                "createdAt": s.createdAt,
            }
            for s in students
        ],
        "totalCount": total,
    }


@app.get("/api/admin/students/{student_id}")
async def get_admin_student(student_id: str):
    student = await prisma_db.student.find_first(where={"id": student_id})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@app.get("/api/admin/instructors/{instructor_id}")
async def get_admin_instructor(instructor_id: str):
    instructor = await prisma_db.instructor.find_first(where={"id": instructor_id})
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")
    return instructor


# =====================================================================
# Admin Roadmap CRUD (manual roadmaps, studentId = None = public/template)
# =====================================================================

class RoadmapCreateUpdateRequest(BaseModel):
    roadmapTitle: str
    roadmapDescription: Optional[str] = ""
    roadmapStatus: Optional[str] = "DRAFT"
    difficultyLevel: Optional[str] = ""
    courseIds: List[str] = []


def serialize_admin_roadmap(r):
    return {
        "id": r.id,
        "roadmapTitle": r.roadmapTitle,
        "roadmapDescription": r.roadmapDescription,
        "roadmapStatus": r.roadmapStatus,
        "totalCourse": r.totalCourse,
        "difficultyLevel": r.difficultyLevel,
        "courseSequence": r.courseSequence or [],
        "createdAt": r.createdAt,
    }


@app.get("/api/admin/roadmaps")
def list_admin_roadmaps(search: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(RoadmapDB).filter(RoadmapDB.studentId.is_(None))
    if search:
        query = query.filter(RoadmapDB.roadmapTitle.ilike(f"%{search}%"))
    roadmaps = query.order_by(RoadmapDB.createdAt.desc()).all()
    return {"roadmaps": [serialize_admin_roadmap(r) for r in roadmaps]}


@app.get("/api/admin/roadmaps/{roadmap_id}")
def get_admin_roadmap(roadmap_id: str, db: Session = Depends(get_db)):
    roadmap = db.get(RoadmapDB, roadmap_id)
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")
    return {"roadmap": serialize_admin_roadmap(roadmap)}


@app.post("/api/admin/roadmaps")
def create_admin_roadmap(payload: RoadmapCreateUpdateRequest, db: Session = Depends(get_db)):
    roadmap = RoadmapDB(
        studentId=None,
        roadmapTitle=payload.roadmapTitle,
        roadmapDescription=payload.roadmapDescription,
        roadmapStatus=payload.roadmapStatus,
        totalCourse=len(payload.courseIds),
        difficultyLevel=payload.difficultyLevel,
        courseSequence=payload.courseIds,
        isAiGenerated=False,
    )
    db.add(roadmap)
    db.commit()
    db.refresh(roadmap)
    return {"roadmap": serialize_admin_roadmap(roadmap)}


@app.put("/api/admin/roadmaps/{roadmap_id}")
def update_admin_roadmap(roadmap_id: str, payload: RoadmapCreateUpdateRequest, db: Session = Depends(get_db)):
    roadmap = db.get(RoadmapDB, roadmap_id)
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")
    roadmap.roadmapTitle = payload.roadmapTitle
    roadmap.roadmapDescription = payload.roadmapDescription
    roadmap.roadmapStatus = payload.roadmapStatus
    roadmap.difficultyLevel = payload.difficultyLevel
    roadmap.courseSequence = payload.courseIds
    roadmap.totalCourse = len(payload.courseIds)
    db.commit()
    db.refresh(roadmap)
    return {"roadmap": serialize_admin_roadmap(roadmap)}


@app.delete("/api/admin/roadmaps/{roadmap_id}")
def delete_admin_roadmap(roadmap_id: str, db: Session = Depends(get_db)):
    roadmap = db.get(RoadmapDB, roadmap_id)
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")
    db.delete(roadmap)
    db.commit()
    return {"message": "Deleted"}





@app.get("/api/admin/companies")
async def list_admin_companies(search: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(CompanyDB)
    if search:
        query = query.filter(CompanyDB.name.ilike(f"%{search}%"))
    companies = query.order_by(CompanyDB.createdAt.desc()).all()
 
    instructor_ids = list({c.instructorId for c in companies})
    instructors_by_id = {}
    if instructor_ids:
        try:
            instructors = await prisma_db.instructor.find_many(where={"id": {"in": instructor_ids}})
            for ins in instructors:
                instructors_by_id[ins.id] = f"{ins.firstName or ''} {ins.lastName or ''}".strip()
        except Exception:
            pass
 
    result = []
    for c in companies:
        employee_count = (
            db.query(CompanyEmployeeDB).filter(CompanyEmployeeDB.companyId == c.id).count()
        )
        course_count = db.query(CompanyCourseDB).filter(CompanyCourseDB.companyId == c.id).count()
        result.append(
            {
                "id": c.id,
                "name": c.name,
                "instructorName": instructors_by_id.get(c.instructorId, "Unknown"),
                "planTier": c.planTier,
                "invitationLimit": c.invitationLimit,
                "employeeCount": employee_count,
                "courseCount": course_count,
                "createdAt": c.createdAt.isoformat() if c.createdAt else None,
            }
        )
 
    return {"companies": result, "totalCount": len(result)}
 
# =====================================================================
# Ye purana "/api/admin/payments" route DELETE karke, iski jagah ye
# nayi wali lagao. Ye InvoiceDB (students) aur PaymentDB (companies)
# dono se data combine karke deti hai.
# =====================================================================

@app.get("/api/admin/payments")
async def list_admin_payments(
    search: Optional[str] = None, payerType: Optional[str] = None, db: Session = Depends(get_db)
):
    combined = []

    # ---- Company payments (PaymentDB) ----
    if payerType in (None, "company"):
        company_payments = db.query(PaymentDB).filter(PaymentDB.payerType == "company").all()
        for p in company_payments:
            combined.append(
                {
                    "id": p.id,
                    "payerType": "company",
                    "payerName": p.payerName,
                    "paymentType": p.paymentType,
                    "referenceTitle": p.referenceTitle,
                    "amount": p.amount or 0,
                    "createdAt": p.createdAt,
                }
            )

    # ---- Student payments (InvoiceDB, sirf successful wale) ----
    if payerType in (None, "student"):
        invoices = db.query(InvoiceDB).filter(InvoiceDB.isSuccessful == True).all()  # noqa: E712

        student_ids = list({i.studentId for i in invoices if i.studentId})
        course_ids = list({i.courseId for i in invoices if i.courseId})

        students_by_id = {}
        if student_ids:
            try:
                students = await prisma_db.student.find_many(where={"id": {"in": student_ids}})
                for s in students:
                    students_by_id[s.id] = f"{s.firstName or ''} {s.lastName or ''}".strip()
            except Exception:
                pass

        courses_by_id = {}
        if course_ids:
            courses = db.query(CourseDB).filter(CourseDB.id.in_(course_ids)).all()
            for c in courses:
                courses_by_id[c.id] = c.title

        for i in invoices:
            combined.append(
                {
                    "id": i.id,
                    "payerType": "student",
                    "payerName": students_by_id.get(i.studentId, "Unknown"),
                    "paymentType": "course_purchase",
                    "referenceTitle": courses_by_id.get(i.courseId, "Unknown Course"),
                    "amount": i.totalAmount or i.invoiceAmount or 0,
                    "createdAt": i.invoiceCompletedAt or i.invoiceDate,
                }
            )

    if search:
        s = search.lower()
        combined = [
            c
            for c in combined
            if s in (c["payerName"] or "").lower() or s in (c["referenceTitle"] or "").lower()
        ]

    combined.sort(key=lambda x: x["createdAt"] or datetime.min, reverse=True)
    total_revenue = sum(c["amount"] or 0 for c in combined)

    return {
        "payments": [
            {**c, "createdAt": c["createdAt"].isoformat() if c["createdAt"] else None} for c in combined
        ],
        "totalCount": len(combined),
        "totalRevenue": total_revenue,
    }