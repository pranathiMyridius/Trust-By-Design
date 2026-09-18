import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi import File, UploadFile
from sqlalchemy.orm import Session
from fastapi import Form
from app.database import get_db
from app.models.assessment import Assessment
from app.schemas.assessment import (
    AssessmentCreate,
    AssessmentResponse,
)
from app.risk_engine.engine import RiskEngine
from app.langgraph.service import run_risk_assessment_workflow
from app.risk_engine.scoring import (
    calculate_overall_score,
    determine_risk_level,
)
from app.models.risk_result import RiskResult
from app.schemas.risk_result import RiskResultResponse
from app.models.assessment_document import AssessmentDocument
from app.file_processing.extractor import extract_text
from app.schemas.assessment_document import (
    AssessmentDocumentResponse,
)
from app.models.assessment_intelligence import AssessmentIntelligence
from app.schemas.assessment_intelligence import AssessmentIntelligenceResponse
from app.models.audit_event import AuditEvent
from app.schemas.audit_event import AuditEventResponse
from app.services.audit_service import log_audit_event
from app.services.audit_service import log_audit_event, AuditAction
from app.schemas.assessment import AssessmentUpdate
from app.file_processing.storage import save_file
from fastapi.responses import FileResponse
from app.document_analysis.analyzer import analyze_document_text
from app.schemas.assessment_challenge import (
    AssessmentChallengeResponse,
    ChallengeFinding,
    ChallengeUpdate,
)
from app.models.assessment_challenge import AssessmentChallenge
from app.schemas.assessment_fcrm_review import (
    FcrmReviewResponse,
    FcrmReviewUpdate,
)
from app.models.assessment_fcrm_review import AssessmentFcrmReview
import json
import mimetypes

MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

router = APIRouter(
    prefix="/api/assessments",
    tags=["Assessments"],
)


@router.post(
    "",
    response_model=AssessmentResponse,
    status_code=201,
)
def create_assessment(
    assessment_data: AssessmentCreate,
    db: Session = Depends(get_db),
):
    assessment = Assessment(
        title=assessment_data.title,
        change_type=assessment_data.change_type,
        description=assessment_data.description,
        evidence=assessment_data.evidence,
    )

    db.add(assessment)
    db.flush()
    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.CREATED,
        previous_status=None,
        new_status=assessment.status,
        details="Assessment created manually.",
    )

    db.commit()
    db.refresh(assessment)

    return assessment


@router.get(
    "",
    response_model=list[AssessmentResponse],
)
def get_assessments(
    db: Session = Depends(get_db),
):
    return (
        db.query(Assessment)
        .order_by(Assessment.created_at.desc())
        .all()
    )


@router.get(
    "/{assessment_id}",
    response_model=AssessmentResponse,
)
def get_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    return assessment


@router.patch(
    "/{assessment_id}/status",
    response_model=AssessmentResponse,
)
def update_assessment_status(
    assessment_id: int,
    status: str,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )
    allowed_statuses = {
        "UNDER_REVIEW",
        "APPROVED",
        "REMEDIATION",
        "REJECTED",
        "DRAFT",
    }

    if status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid assessment status",
        )

    previous_status = assessment.status

    if status == "DRAFT":
        if previous_status != "REMEDIATION":
            raise HTTPException(
                status_code=400,
                detail="Only a REMEDIATION assessment can be reverted to DRAFT.",
            )

    # READY_FOR_REVIEW → UNDER_REVIEW
    if status == "UNDER_REVIEW":
        if previous_status != "READY_FOR_REVIEW":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Assessment must be READY_FOR_REVIEW "
                    "before it can move to UNDER_REVIEW."
                ),
            )

    # UNDER_REVIEW → final decision
    if status in {
        "APPROVED",
        "REMEDIATION",
        "REJECTED",
    }:
        if previous_status != "UNDER_REVIEW":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Assessment must be UNDER_REVIEW "
                    "before a final review decision can be made."
                ),
            )

    assessment.status = status

    # Use a more specific audit action for final decisions
    status_to_action = {
    "APPROVED": AuditAction.APPROVAL,
    "REMEDIATION": AuditAction.REMEDIATION,
    "REJECTED": AuditAction.REJECTION,
    }
    action = status_to_action.get(status, AuditAction.STATUS_CHANGE)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=action,
        previous_status=previous_status,
        new_status=status,
        details=(
            f"Status changed from "
            f"{previous_status} to {status}."
        ),
    )

    db.commit()
    db.refresh(assessment)

    return assessment

@router.post(
    "/{assessment_id}/analyze",
    response_model=AssessmentResponse,
)
def analyze_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    """
    Run the assessment through the LangGraph orchestration layer.

    LangGraph currently orchestrates the existing deterministic risk
    engine; it does not duplicate or replace the business rules.
    Claude/LLM nodes can be added later without changing this API contract.
    """
    try:
        run_risk_assessment_workflow(
            assessment_id=assessment_id,
            db=db,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Risk assessment workflow failed: {str(exc)}",
        )

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    return assessment


@router.post("/analyze-document")
async def analyze_uploaded_document(
    file: UploadFile = File(...),
):
    allowed_extensions = {
        ".docx",
        ".xlsx",
        ".pdf",
        ".txt",
        ".csv",
    }

    filename = file.filename or ""

    extension = (
        "." + filename.split(".")[-1].lower()
        if "." in filename
        else ""
    )

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. "
                "Supported files: DOCX, XLSX, PDF, TXT, CSV."
            ),
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    try:
        extracted_text = extract_text(
            filename=filename,
            file_content=file_content,
        )
        

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "No readable text was found "
                    "in the uploaded file."
                ),
            )

        from app.document_analysis.analyzer import (
            analyze_document_text,
        )

        structured_data = analyze_document_text(
            extracted_text
        )

        return {
            "filename": filename,
            "extracted_text": extracted_text,
            "assessment": structured_data.model_dump(),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to analyze document: {str(exc)}"
            ),
        )


@router.post(
    "/create-from-document",
    response_model=AssessmentResponse,
)
async def create_assessment_from_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    allowed_extensions = {
        ".docx",
        ".xlsx",
        ".pdf",
        ".txt",
        ".csv",
    }

    filename = file.filename or ""

    extension = (
        "." + filename.split(".")[-1].lower()
        if "." in filename
        else ""
    )

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. "
                "Supported files: DOCX, XLSX, PDF, TXT, CSV."
            ),
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    try:
        # -----------------------------------------
        # 1. Extract text from document
        # -----------------------------------------
        extracted_text = extract_text(
            filename=filename,
            file_content=file_content,
        )

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No readable text was found in the uploaded file.",
            )

        # -----------------------------------------
        # 2. Analyze extracted document
        # -----------------------------------------
        from app.document_analysis.analyzer import analyze_document_text

        structured_data = analyze_document_text(
            extracted_text
        )

        # -----------------------------------------
        # 3. Create Assessment
        # -----------------------------------------
        assessment = Assessment(
            title=structured_data.title,
            change_type=structured_data.change_type,
            description=structured_data.business_description,
            evidence=structured_data.evidence,
            status="DRAFT",
        )

        db.add(assessment)

        # Get assessment.id before creating child records
        db.flush()

        # -----------------------------------------
        # 4. Save source document
        # -----------------------------------------
        document = AssessmentDocument(
            assessment_id=assessment.id,
            filename=filename,
            file_type=extension,
            extracted_text=extracted_text,
        )

        db.add(document)

        # -----------------------------------------
        # 5. Save extracted intelligence
        # -----------------------------------------
        intelligence = AssessmentIntelligence(
            assessment_id=assessment.id,
            business_line=structured_data.business_line,
            transaction_volume=structured_data.transaction_volume,
            average_transaction_size=structured_data.average_transaction_size,
            maximum_transaction_limit=structured_data.maximum_transaction_limit,
        )

        intelligence.set_list(
            "channels",
            structured_data.channels,
        )

        intelligence.set_list(
            "countries",
            structured_data.countries,
        )

        intelligence.set_list(
            "customer_segments",
            structured_data.customer_segments,
        )

        intelligence.set_list(
            "third_party_vendors",
            structured_data.third_party_vendors,
        )

        intelligence.set_list(
            "data_shared",
            structured_data.data_shared,
        )

        intelligence.set_list(
            "technologies",
            structured_data.technologies,
        )

        intelligence.set_list(
            "regulatory_considerations",
            structured_data.regulatory_considerations,
        )

        intelligence.set_list(
            "existing_controls",
            structured_data.existing_controls,
        )

        intelligence.set_list(
            "additional_risk_factors",
            structured_data.additional_risk_factors,
        )

        db.add(intelligence)

        # -----------------------------------------
        # 6. Log audit event for creation
        # -----------------------------------------
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.CREATED,
            previous_status=None,
            new_status=assessment.status,
            details=f"Assessment created from uploaded document: {filename}.",
        )

        # -----------------------------------------
        # 7. Save everything
        # -----------------------------------------
        db.commit()

        # Refresh assessment from database
        db.refresh(assessment)

        # -----------------------------------------
        # 8. IMPORTANT: return assessment
        # -----------------------------------------
        return assessment

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to create assessment from document: "
                f"{str(exc)}"
            ),
        )


@router.get(
    "/{assessment_id}/documents",
    response_model=list[AssessmentDocumentResponse],
)
def get_assessment_documents(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment_id
        )
        .order_by(AssessmentDocument.created_at.desc())
        .all()
    )

    return documents


@router.get(
    "/{assessment_id}/intelligence",
    response_model=AssessmentIntelligenceResponse,
)
def get_assessment_intelligence(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id == assessment_id
        )
        .first()
    )

    if not intelligence:
        raise HTTPException(
            status_code=404,
            detail="Assessment intelligence not found.",
        )

    return AssessmentIntelligenceResponse(
        id=intelligence.id,
        assessment_id=intelligence.assessment_id,
        business_line=intelligence.business_line,
        channels=intelligence.get_list("channels"),
        countries=intelligence.get_list("countries"),
        customer_segments=intelligence.get_list("customer_segments"),
        transaction_volume=intelligence.transaction_volume,
        average_transaction_size=intelligence.average_transaction_size,
        maximum_transaction_limit=intelligence.maximum_transaction_limit,
        third_party_vendors=intelligence.get_list(
            "third_party_vendors"
        ),
        data_shared=intelligence.get_list("data_shared"),
        technologies=intelligence.get_list("technologies"),
        regulatory_considerations=intelligence.get_list(
            "regulatory_considerations"
        ),
        existing_controls=intelligence.get_list(
            "existing_controls"
        ),
        additional_risk_factors=intelligence.get_list(
            "additional_risk_factors"
        ),
        created_at=intelligence.created_at,
    )


@router.get(
    "/{assessment_id}/audit",
    response_model=list[AuditEventResponse],
)
def get_assessment_audit(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.assessment_id == assessment_id
        )
        .order_by(
            AuditEvent.created_at.desc()
        )
        .all()
    )
@router.get(
    "/audit/all",
    response_model=list[AuditEventResponse],
)
def get_all_audit_events(
    db: Session = Depends(get_db),
):
    return (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc())
        .all()
    )
from app.schemas.assessment import AssessmentUpdate  # you'll need to add this schema

@router.patch(
    "/{assessment_id}",
    response_model=AssessmentResponse,
)
def update_assessment(
    assessment_id: int,
    assessment_data: AssessmentUpdate,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    if assessment.status not in {"DRAFT", "REMEDIATION"}:
        raise HTTPException(
            status_code=400,
            detail="Assessment can only be edited while in DRAFT or REMEDIATION.",
        )

    assessment.title = assessment_data.title
    assessment.change_type = assessment_data.change_type
    assessment.description = assessment_data.description
    assessment.evidence = assessment_data.evidence

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=assessment.status,
        new_status=assessment.status,
        details="Assessment details edited.",
    )

    db.commit()
    db.refresh(assessment)
    return assessment


@router.post(
    "/{assessment_id}/documents",
    response_model=AssessmentDocumentResponse,
)
async def upload_assessment_document(
    assessment_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    if assessment.status not in {"DRAFT", "REMEDIATION"}:
        raise HTTPException(
            status_code=400,
            detail="Documents can only be uploaded while in DRAFT or REMEDIATION.",
        )

    allowed_extensions = {".docx", ".xlsx", ".pdf", ".txt", ".csv"}
    filename = file.filename or ""
    extension = "." + filename.split(".")[-1].lower() if "." in filename else ""

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Supported files: DOCX, XLSX, PDF, TXT, CSV.",
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    extracted_text = extract_text(filename=filename, file_content=file_content)
    saved_path = save_file(assessment_id, filename, file_content)

    document = AssessmentDocument(
        assessment_id=assessment.id,
        filename=filename,
        file_type=extension,
        extracted_text=extracted_text,
        file_path=saved_path,
    )

    db.add(document)

    log_audit_event(
        db=db,
        assessment_id=assessment.id,
        action=AuditAction.STATUS_CHANGE,
        previous_status=assessment.status,
        new_status=assessment.status,
        details=f"Document uploaded: {filename}.",
    )

    db.commit()
    db.refresh(document)
    return document
@router.get(
    "/{assessment_id}/risk-results",
    response_model=list[RiskResultResponse],
)
def get_assessment_risk_results(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    return (
        db.query(RiskResult)
        .filter(RiskResult.assessment_id == assessment_id)
        .all()
    )
@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: int,
    db: Session = Depends(get_db),
):
    document = (
        db.query(AssessmentDocument)
        .filter(AssessmentDocument.id == document_id)
        .first()
    )

    if not document or not document.file_path:
        raise HTTPException(status_code=404, detail="Document file not found")

    if not os.path.exists(document.file_path):
        raise HTTPException(status_code=404, detail="File no longer exists on disk")

    extension = os.path.splitext(document.filename)[1].lower()
    media_type = MIME_TYPES.get(extension) or mimetypes.guess_type(document.filename)[0] or "application/octet-stream"

    # Note: FileResponse's `filename` argument always sends
    # `Content-Disposition: attachment`, which forces the browser to
    # download the file even when the frontend opens it with
    # window.open() for the "View" action. Set the header explicitly to
    # `inline` so the browser renders the file itself; the frontend's
    # separate "Download" button still saves it via the <a download>
    # attribute regardless of this header.
    return FileResponse(
        path=document.file_path,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{document.filename}"'
        },
    )


# @router.post(
#     "/create-with-document",
#     response_model=AssessmentResponse,
# )
# async def create_assessment_with_document(
#     title: str = Form(...),
#     change_type: str = Form(...),
#     description: str = Form(...),
#     evidence: str = Form(...),
#     file: UploadFile = File(...),
#     db: Session = Depends(get_db),
# ):
#     allowed_extensions = {".docx", ".xlsx", ".pdf", ".txt", ".csv"}
#     filename = file.filename or ""
#     extension = "." + filename.split(".")[-1].lower() if "." in filename else ""

#     if extension not in allowed_extensions:
#         raise HTTPException(
#             status_code=400,
#             detail="Unsupported file type. Supported files: DOCX, XLSX, PDF, TXT, CSV.",
#         )

#     file_content = await file.read()

#     if not file_content:
#         raise HTTPException(status_code=400, detail="Uploaded file is empty.")

#     try:
#         extracted_text = extract_text(filename=filename, file_content=file_content)

#         # -----------------------------------------
#         # 1. Create assessment using the user's
#         #    reviewed/edited field values
#         # -----------------------------------------
#         assessment = Assessment(
#             title=title,
#             change_type=change_type,
#             description=description,
#             evidence=evidence,
#             status="DRAFT",
#         )

#         db.add(assessment)
#         db.flush()
#         print("STEP 2: assessment created, id =", assessment.id)

#         # -----------------------------------------
#         # 2. Save the original file
#         # -----------------------------------------
#         saved_path = save_file(assessment.id, filename, file_content)

#         document = AssessmentDocument(
#             assessment_id=assessment.id,
#             filename=filename,
#             file_type=extension,
#             extracted_text=extracted_text,
#             file_path=saved_path,
#         )

#         db.add(document)
#         print("STEP 3: document added")

#         # -----------------------------------------
#         # 3. Log audit event
#         # -----------------------------------------
#         from app.document_analysis.analyzer import analyze_document_text
#         print("STEP 4: about to call analyze_document_text")
#         structured_data = analyze_document_text(extracted_text)
#         print("STEP 5: structured_data received:", structured_data.business_line)
#         log_audit_event(
#             db=db,
#             assessment_id=assessment.id,
#             action=AuditAction.CREATED,
#             previous_status=None,
#             new_status=assessment.status,
#             details=f"Assessment created from uploaded document: {filename}.",
#         )

#         db.commit()
#         db.refresh(assessment)
#         print("STEP 4: db committed and assessment refreshed")
#         return assessment

#     except HTTPException:
#         db.rollback()
#         raise

#     except Exception as exc:
#         db.rollback()
#         raise HTTPException(
#             status_code=500,
#             detail=f"Unable to create assessment: {str(exc)}",
#         )
@router.post(
    "/create-with-document",
    response_model=AssessmentResponse,
)
async def create_assessment_with_document(
    title: str = Form(...),
    change_type: str = Form(...),
    description: str = Form(...),
    evidence: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    allowed_extensions = {".docx", ".xlsx", ".pdf", ".txt", ".csv"}
    filename = file.filename or ""
    extension = "." + filename.split(".")[-1].lower() if "." in filename else ""

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Supported files: DOCX, XLSX, PDF, TXT, CSV.",
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        extracted_text = extract_text(filename=filename, file_content=file_content)

        # -----------------------------------------
        # 1. Create assessment using the user's
        #    reviewed/edited field values
        # -----------------------------------------
        assessment = Assessment(
            title=title,
            change_type=change_type,
            description=description,
            evidence=evidence,
            status="DRAFT",
        )

        db.add(assessment)
        db.flush()

        # -----------------------------------------
        # 2. Save the original file
        # -----------------------------------------
        saved_path = save_file(assessment.id, filename, file_content)

        document = AssessmentDocument(
            assessment_id=assessment.id,
            filename=filename,
            file_type=extension,
            extracted_text=extracted_text,
            file_path=saved_path,
        )

        db.add(document)

        # -----------------------------------------
        # 3. Extract structured business intelligence
        #    from the raw document text
        # -----------------------------------------
        from app.document_analysis.analyzer import analyze_document_text

        structured_data = analyze_document_text(extracted_text)

        intelligence = AssessmentIntelligence(
            assessment_id=assessment.id,
            business_line=structured_data.business_line,
            transaction_volume=structured_data.transaction_volume,
            average_transaction_size=structured_data.average_transaction_size,
            maximum_transaction_limit=structured_data.maximum_transaction_limit,
        )

        intelligence.set_list("channels", structured_data.channels)
        intelligence.set_list("countries", structured_data.countries)
        intelligence.set_list("customer_segments", structured_data.customer_segments)
        intelligence.set_list("third_party_vendors", structured_data.third_party_vendors)
        intelligence.set_list("data_shared", structured_data.data_shared)
        intelligence.set_list("technologies", structured_data.technologies)
        intelligence.set_list(
            "regulatory_considerations", structured_data.regulatory_considerations
        )
        intelligence.set_list("existing_controls", structured_data.existing_controls)
        intelligence.set_list(
            "additional_risk_factors", structured_data.additional_risk_factors
        )

        db.add(intelligence)

        # -----------------------------------------
        # 4. Log audit event
        # -----------------------------------------
        log_audit_event(
            db=db,
            assessment_id=assessment.id,
            action=AuditAction.CREATED,
            previous_status=None,
            new_status=assessment.status,
            details=f"Assessment created from uploaded document: {filename}.",
        )

        db.commit()
        db.refresh(assessment)

        return assessment

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to create assessment: {str(exc)}",
        )
def _get_control_reduction_for_challenge(
    risk_results,
):
    effective_controls = 0
    minor_controls = 0

    effective_dimensions = {
        "CUSTOMER",
        "COMPLIANCE",
        "TECHNOLOGY",
    }

    minor_dimensions = {
        "OPERATIONAL",
        "FINANCIAL",
    }

    for result in risk_results:
        if result.dimension in effective_dimensions:
            effective_controls += 1

        elif result.dimension in minor_dimensions:
            minor_controls += 1

    reduction = (
        effective_controls * 4
        + minor_controls * 2
    )

    return min(reduction, 30)


def _get_risk_level(score: float):
    if score >= 80:
        return "CRITICAL"

    if score >= 60:
        return "HIGH"

    if score >= 30:
        return "MEDIUM"

    return "LOW"


def _build_challenge_findings(
    assessment,
    risk_results,
    intelligence,
):
    findings = []

    critical_results = [
        result
        for result in risk_results
        if result.score >= 80
    ]

    high_results = [
        result
        for result in risk_results
        if 60 <= result.score < 80
    ]

    if critical_results:
        dimensions = ", ".join(
            result.dimension
            for result in critical_results
        )

        findings.append(
            ChallengeFinding(
                title="Critical Risk Dimension Challenge",
                severity="CRITICAL",
                finding=(
                    f"The assessment contains critical risk "
                    f"exposure in {dimensions}. The challenge "
                    f"requires explicit acknowledgement of these "
                    f"risk dimensions before final committee "
                    f"disposition."
                ),
            )
        )

    if high_results:
        dimensions = ", ".join(
            result.dimension
            for result in high_results
        )

        findings.append(
            ChallengeFinding(
                title="High Risk Validation",
                severity="HIGH",
                finding=(
                    f"High risk exposure was identified in "
                    f"{dimensions}. The reviewer should confirm "
                    f"that the mapped controls adequately address "
                    f"the underlying risk drivers."
                ),
            )
        )

    if intelligence:
        countries = intelligence.get_list("countries")

        if len(countries) > 1:
            findings.append(
                ChallengeFinding(
                    title="Geographic Exposure Challenge",
                    severity="HIGH",
                    finding=(
                        f"The assessment involves multiple "
                        f"jurisdictions ({', '.join(countries)}). "
                        f"Cross-border regulatory obligations and "
                        f"country-specific controls should be "
                        f"validated before approval."
                    ),
                )
            )

        vendors = intelligence.get_list(
            "third_party_vendors"
        )

        if vendors:
            findings.append(
                ChallengeFinding(
                    title="Third-Party Dependency Challenge",
                    severity="HIGH",
                    finding=(
                        f"The assessment relies on third-party "
                        f"processor(s): {', '.join(vendors)}. "
                        f"Due diligence, SLA coverage and fallback "
                        f"controls should be confirmed."
                    ),
                )
            )

        data_shared = intelligence.get_list(
            "data_shared"
        )

        if data_shared:
            findings.append(
                ChallengeFinding(
                    title="Customer Data Challenge",
                    severity="HIGH",
                    finding=(
                        "Customer or transaction information is "
                        "shared as part of the proposed change. "
                        "Data protection, access control and "
                        "regulatory requirements should be "
                        "validated."
                    ),
                )
            )

    if assessment.evidence:
        findings.append(
            ChallengeFinding(
                title="Evidence Consistency",
                severity="LOW",
                finding=(
                    "Assessment evidence is available for review. "
                    "Evidence should remain traceable to the "
                    "identified risks and mapped controls."
                ),
            )
        )
    else:
        findings.append(
            ChallengeFinding(
                title="Evidence Consistency",
                severity="HIGH",
                finding=(
                    "No assessment evidence is currently "
                    "available. Additional evidence is required "
                    "before final disposition."
                ),
            )
        )

    if not findings:
        findings.append(
            ChallengeFinding(
                title="General Risk Challenge",
                severity="MEDIUM",
                finding=(
                    "The challenge review did not identify a "
                    "specific critical exception. The reviewer "
                    "should validate the overall risk rationale "
                    "and supporting controls."
                ),
            )
        )

    return findings
@router.get(
    "/{assessment_id}/challenge",
    response_model=AssessmentChallengeResponse,
)
def get_assessment_challenge(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    risk_results = (
        db.query(RiskResult)
        .filter(
            RiskResult.assessment_id == assessment_id
        )
        .all()
    )

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id
            == assessment_id
        )
        .first()
    )

    challenge = (
        db.query(AssessmentChallenge)
        .filter(
            AssessmentChallenge.assessment_id
            == assessment_id
        )
        .first()
    )

    if not challenge:
        challenge = AssessmentChallenge(
            assessment_id=assessment_id,
            challenge_id=f"CHL-{assessment_id:04d}-01",
            status="OPEN",
            challenged_by="FCRM Reviewer",
        )

        db.add(challenge)
        db.commit()
        db.refresh(challenge)

    inherent_score = assessment.overall_score or 0

    control_reduction = (
        _get_control_reduction_for_challenge(
            risk_results
        )
    )

    residual_score = max(
        0,
        inherent_score - control_reduction,
    )

    findings = _build_challenge_findings(
        assessment,
        risk_results,
        intelligence,
    )

    return {
        "id": challenge.id,
        "assessment_id": challenge.assessment_id,
        "challenge_id": challenge.challenge_id,
        "status": challenge.status,
        "outcome": challenge.outcome,
        "comment": challenge.comment,
        "challenged_by": challenge.challenged_by,
        "created_at": challenge.created_at,
        "updated_at": challenge.updated_at,
        "findings": findings,
        "inherent_score": inherent_score,
        "residual_score": residual_score,
        "residual_level": _get_risk_level(
            residual_score
        ),
        "control_reduction": control_reduction,
    }
@router.patch(
    "/{assessment_id}/challenge",
    response_model=AssessmentChallengeResponse,
)
def update_assessment_challenge(
    assessment_id: int,
    payload: ChallengeUpdate,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    if payload.outcome not in {
        "ACCEPTED",
        "REMEDIATION_REQUIRED",
        "ESCALATE",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid challenge outcome",
        )

    if not payload.comment.strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "Challenge commentary is required."
            ),
        )

    challenge = (
        db.query(AssessmentChallenge)
        .filter(
            AssessmentChallenge.assessment_id
            == assessment_id
        )
        .first()
    )

    if not challenge:
        challenge = AssessmentChallenge(
            assessment_id=assessment_id,
            challenge_id=f"CHL-{assessment_id:04d}-01",
        )

        db.add(challenge)

    previous_status = assessment.status

    challenge.outcome = payload.outcome
    challenge.comment = payload.comment
    challenge.status = "COMPLETED"

    if payload.outcome == "REMEDIATION_REQUIRED":
        assessment.status = "REMEDIATION"

    elif payload.outcome in {
        "ACCEPTED",
        "ESCALATE",
    }:
        assessment.status = "READY_FOR_DECISION"

    log_audit_event(
        db=db,
        assessment_id=assessment_id,
        action="CHALLENGE",
        previous_status=previous_status,
        new_status=assessment.status,
        details=(
            f"Challenge {challenge.challenge_id} "
            f"completed with outcome "
            f"{payload.outcome}."
        ),
    )

    db.commit()
    db.refresh(challenge)
    db.refresh(assessment)

    risk_results = (
        db.query(RiskResult)
        .filter(
            RiskResult.assessment_id == assessment_id
        )
        .all()
    )

    intelligence = (
        db.query(AssessmentIntelligence)
        .filter(
            AssessmentIntelligence.assessment_id
            == assessment_id
        )
        .first()
    )

    inherent_score = assessment.overall_score or 0

    control_reduction = (
        _get_control_reduction_for_challenge(
            risk_results
        )
    )

    residual_score = max(
        0,
        inherent_score - control_reduction,
    )

    return {
        "id": challenge.id,
        "assessment_id": challenge.assessment_id,
        "challenge_id": challenge.challenge_id,
        "status": challenge.status,
        "outcome": challenge.outcome,
        "comment": challenge.comment,
        "challenged_by": challenge.challenged_by,
        "created_at": challenge.created_at,
        "updated_at": challenge.updated_at,
        "findings": _build_challenge_findings(
            assessment,
            risk_results,
            intelligence,
        ),
        "inherent_score": inherent_score,
        "residual_score": residual_score,
        "residual_level": _get_risk_level(
            residual_score
        ),
        "control_reduction": control_reduction,
    }

@router.get(
    "/{assessment_id}/fcrm-review",
    response_model=FcrmReviewResponse,
)
def get_fcrm_review(
    assessment_id: int,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    review = (
        db.query(AssessmentFcrmReview)
        .filter(
            AssessmentFcrmReview.assessment_id == assessment_id
        )
        .first()
    )

    if not review:
        # Nothing saved yet — return an empty review rather than 404 so
        # the frontend can always load this endpoint on mount.
        return {
            "assessment_id": assessment_id,
            "justification": "",
            "human_ratings": {},
            "reviewed_by": None,
            "updated_at": None,
        }

    try:
        human_ratings = (
            json.loads(review.human_ratings)
            if review.human_ratings
            else {}
        )
    except (TypeError, ValueError):
        human_ratings = {}

    return {
        "assessment_id": review.assessment_id,
        "justification": review.justification or "",
        "human_ratings": human_ratings,
        "reviewed_by": review.reviewed_by,
        "updated_at": review.updated_at,
    }


@router.patch(
    "/{assessment_id}/fcrm-review",
    response_model=FcrmReviewResponse,
)
def update_fcrm_review(
    assessment_id: int,
    payload: FcrmReviewUpdate,
    db: Session = Depends(get_db),
):
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .first()
    )

    if not assessment:
        raise HTTPException(
            status_code=404,
            detail="Assessment not found",
        )

    review = (
        db.query(AssessmentFcrmReview)
        .filter(
            AssessmentFcrmReview.assessment_id == assessment_id
        )
        .first()
    )

    encoded_ratings = json.dumps(payload.human_ratings)

    if not review:
        review = AssessmentFcrmReview(
            assessment_id=assessment_id,
            justification=payload.justification,
            human_ratings=encoded_ratings,
            reviewed_by="FCRM Reviewer",
        )
        db.add(review)
    else:
        review.justification = payload.justification
        review.human_ratings = encoded_ratings

    log_audit_event(
        db,
        assessment_id=assessment_id,
        action=AuditAction.APPROVAL,
        actor=review.reviewed_by or "FCRM Reviewer",
        details="FCRM review saved (justification and rating reconciliation updated).",
    )

    db.commit()
    db.refresh(review)

    return {
        "assessment_id": review.assessment_id,
        "justification": review.justification or "",
        "human_ratings": payload.human_ratings,
        "reviewed_by": review.reviewed_by,
        "updated_at": review.updated_at,
    }
