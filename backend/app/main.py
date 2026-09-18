from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.models.assessment import Assessment
from app.models.risk_result import RiskResult
from app.models.assessment_document import AssessmentDocument
from app.api.assessments import router as assessment_router
from app.models.assessment_intelligence import AssessmentIntelligence
from app.models.audit_event import AuditEvent
from app.models.assessment_challenge import AssessmentChallenge
from app.models.assessment_fcrm_review import AssessmentFcrmReview


Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="Risk Assessment Workbench",
    description="Enterprise risk assessment platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins =[
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://localhost:5173",
        "http://localhost:1574",
        "http://127.0.0.1:1574"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(assessment_router)


@app.get("/")
def root():
    return {
        "application": "Risk Assessment Workbench",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }