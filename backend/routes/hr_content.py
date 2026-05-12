"""
HR content authoring routes.

Lets HR admins create, list, edit, and delete the content that drives the
candidate test:
  - Passages (the reading-section texts)
  - Questions (reading_comp + standalone grammar/vocabulary/fill_blank)
  - Writing topics (essay prompts)
  - Speaking topics (impromptu prompts)

Plus CSV bulk import for the three biggest categories. Speaking topics and
reading-comp questions are intentionally NOT bulk-importable:
  - speaking topics: typically only ~8 of them, single form is faster than CSV
  - reading_comp:    requires a parent passage_id which is awkward in CSV format

DELETE policy: hard-delete is rejected if the entity is referenced by an
existing invitation's assigned_* arrays. The endpoint returns 409 with a
clear message; HR can either un-assign by waiting for those invitations to
expire or contact the developer to handle the cleanup explicitly.
"""
from io import StringIO
import csv
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from database import get_db
from models import (
    HRAdmin, Passage, Question, SpeakingTopic, WritingTopic, Invitation,
    OrganizationContentDisable,
)
from auth import require_principal, Principal
from tenancy import (
    tenant_scope_content_read,   # list-time WHERE filter ("own-org + global")
    assert_can_edit_content,     # single-row guard for update/delete/toggle
    new_content_org_id,          # computes the org_id to stamp on new rows
)
import schemas


router = APIRouter(prefix="/api/hr/content", tags=["hr-content"])


# ============================================================================
# Helpers
# ============================================================================
#
# Content authoring is gated on require_principal(allow=("super","admin","hr"),
# strict=True). The same dependency applies to every endpoint below — content
# management is a unified surface across roles, with tenancy scoping handled
# by tenant_scope_content_read / assert_can_edit_content based on principal.role.
#
# Why include 'hr' alongside 'admin' and 'super':
#   - HR authors content for their own org (own-org + global reads, own-org writes).
#   - Admin sees the same surface but is also the rolewith broader visibility
#     within the org. Same content rules apply (own-org + global reads, own-org
#     writes — global is super-only to edit).
#   - Super sees and edits everything, including the global pool.
#
# strict=True blocks users on a temp password from authoring content. They
# need to clear must_change_password via /api/hr/change-password or
# /api/admin/change-password first.

ALLOWED_DIFFICULTIES = {"intermediate", "expert"}
ALLOWED_QUESTION_TYPES = {"reading_comp", "grammar", "vocabulary", "fill_blank"}


def _validate_difficulty(value: str) -> str:
    if value not in ALLOWED_DIFFICULTIES:
        raise HTTPException(
            status_code=422,
            detail=f"difficulty must be one of: {sorted(ALLOWED_DIFFICULTIES)}",
        )
    return value


def _validate_question_type(value: str) -> str:
    if value not in ALLOWED_QUESTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"question_type must be one of: {sorted(ALLOWED_QUESTION_TYPES)}",
        )
    return value


def _validate_options(options: list, correct_answer: int) -> None:
    """Enforce: exactly 4 non-empty options, correct_answer in 0..3."""
    if not isinstance(options, list) or len(options) != 4:
        raise HTTPException(status_code=422, detail="options must be a list of exactly 4 strings")
    for i, opt in enumerate(options):
        if not isinstance(opt, str) or not opt.strip():
            raise HTTPException(status_code=422, detail=f"options[{i}] must be a non-empty string")
    if not (0 <= correct_answer <= 3):
        raise HTTPException(status_code=422, detail="correct_answer must be between 0 and 3")


def _check_question_in_use(db: Session, question_id: int) -> None:
    """Raise 409 if any invitation has this question in its assigned_question_ids.

    Note: assigned_question_ids is a JSON column (list[int]), not a Postgres
    ARRAY. SQLAlchemy's .contains() generates a LIKE query against JSON which
    Postgres rejects. We instead pull the candidate invitations into Python
    and filter there. Cheap because most deployments have <1000 invitations
    and DELETE is rare.
    """
    invitations = db.query(Invitation).filter(
        Invitation.assigned_question_ids.isnot(None)
    ).all()
    for inv in invitations:
        ids = inv.assigned_question_ids or []
        if question_id in ids:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete: question is assigned to one or more existing invitations. "
                    "Wait for those invitations to expire or be submitted, then try again."
                ),
            )


def _check_passage_in_use(db: Session, passage_id: int) -> None:
    refs = (
        db.query(Invitation.id)
        .filter(Invitation.passage_id == passage_id)
        .first()
    )
    if refs is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete: passage is assigned to one or more existing invitations. "
                f"Wait for those invitations to expire or be submitted, then try again."
            ),
        )
    q_refs = db.query(Question.id).filter(Question.passage_id == passage_id).first()
    if q_refs is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete: this passage has reading-comprehension questions "
                f"linked to it. Delete those questions first, then retry."
            ),
        )


def _check_speaking_topic_in_use(db: Session, topic_id: int) -> None:
    """Raise 409 if any invitation has this topic in its assigned_topic_ids.
    Same reason as _check_question_in_use — JSON column, filter in Python.
    """
    invitations = db.query(Invitation).filter(
        Invitation.assigned_topic_ids.isnot(None)
    ).all()
    for inv in invitations:
        ids = inv.assigned_topic_ids or []
        if topic_id in ids:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete: speaking topic is assigned to one or more existing invitations. "
                    "Wait for those invitations to expire or be submitted, then try again."
                ),
            )


def _check_writing_topic_in_use(db: Session, topic_id: int) -> None:
    refs = (
        db.query(Invitation.id)
        .filter(Invitation.assigned_writing_topic_id == topic_id)
        .first()
    )
    if refs is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete: writing topic is assigned to one or more existing invitations. "
                f"Wait for those invitations to expire or be submitted, then try again."
            ),
        )


# ============================================================================
# PASSAGES
# ============================================================================

@router.get("/passages", response_model=List[schemas.PassageOut])
def list_passages(
    difficulty: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Tenancy: returns own-org passages + global (NULL org_id) passages.
    Globally-seeded content (NULL org_id from Stixis install) is visible
    to every org so customers don't have to re-author it. Super sees
    every org's passages too.
    """
    q = tenant_scope_content_read(
        db.query(Passage).filter(Passage.deleted_at.is_(None)),
        Passage,
        p,
    ).order_by(Passage.id.desc())
    if difficulty:
        _validate_difficulty(difficulty)
        q = q.filter(Passage.difficulty == difficulty)
    return q.all()


@router.post("/passages", response_model=schemas.PassageOut, status_code=201)
def create_passage(
    payload: schemas.PassageCreate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Tenancy: new passages are stamped with the caller's organization_id.
    HR/admin can never author into the global pool (only super can).
    """
    _validate_difficulty(payload.difficulty)
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="title cannot be empty")
    if len(payload.body.split()) < 50:
        raise HTTPException(status_code=422, detail="body must be at least 50 words")

    passage = Passage(
        title=payload.title.strip(),
        body=payload.body,
        difficulty=payload.difficulty,
        topic=payload.topic.strip() if payload.topic else None,
        word_count=len(payload.body.split()),
        organization_id=new_content_org_id(p),
    )
    db.add(passage)
    db.commit()
    db.refresh(passage)
    return passage


@router.patch("/passages/{passage_id}", response_model=schemas.PassageOut)
def update_passage(
    passage_id: int,
    payload: schemas.PassageUpdate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Tenancy: can edit only own-org passages.
      - Global content (NULL org) → 403 with "ask super" message.
      - Cross-org content → 404 (don't leak existence).
      - Own org → allowed.
    """
    passage = db.query(Passage).filter(Passage.id == passage_id).first()
    if not passage:
        raise HTTPException(status_code=404, detail="passage not found")
    assert_can_edit_content(passage, p)

    if payload.title is not None:
        if not payload.title.strip():
            raise HTTPException(status_code=422, detail="title cannot be empty")
        passage.title = payload.title.strip()
    if payload.body is not None:
        if len(payload.body.split()) < 50:
            raise HTTPException(status_code=422, detail="body must be at least 50 words")
        passage.body = payload.body
        passage.word_count = len(payload.body.split())
    if payload.difficulty is not None:
        _validate_difficulty(payload.difficulty)
        passage.difficulty = payload.difficulty
    if payload.topic is not None:
        passage.topic = payload.topic.strip() or None

    db.commit()
    db.refresh(passage)
    return passage


@router.delete("/passages/{passage_id}", status_code=204)
def delete_passage(
    passage_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Soft delete — sets deleted_at timestamp. Hidden from HR list afterward.
    Existing invitations are unaffected because they snapshot assigned IDs
    at creation time. _check_passage_in_use removed: soft delete cannot
    cause FK violations.

    Tenancy: same rules as update — own-org only, 403 on global, 404 on
    cross-org."""
    passage = db.query(Passage).filter(
        Passage.id == passage_id,
        Passage.deleted_at.is_(None),
    ).first()
    if not passage:
        raise HTTPException(status_code=404, detail="passage not found")
    assert_can_edit_content(passage, p)
    passage.deleted_at = datetime.utcnow()
    db.commit()
    return None


@router.post("/passages/{passage_id}/toggle-disabled", response_model=schemas.PassageOut)
def toggle_passage_disabled(
    passage_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Toggle disabled state. NULL → set to now (disabled). Non-NULL → set
    to NULL (re-enabled). New invitations skip disabled items; existing
    invitations are unaffected.

    Tenancy: same rules as update — own-org only."""
    passage = db.query(Passage).filter(
        Passage.id == passage_id,
        Passage.deleted_at.is_(None),
    ).first()
    if not passage:
        raise HTTPException(status_code=404, detail="passage not found")
    assert_can_edit_content(passage, p)
    passage.disabled_at = None if passage.disabled_at else datetime.utcnow()
    db.commit()
    db.refresh(passage)
    return passage


@router.post("/passages/bulk", response_model=schemas.BulkImportResult)
async def bulk_import_passages(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Bulk-import passages from CSV. Expected columns:
        title, body, difficulty, topic
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="upload must be a .csv file")

    content = (await file.read()).decode("utf-8")
    reader = csv.DictReader(StringIO(content))
    expected_cols = {"title", "body", "difficulty", "topic"}
    if reader.fieldnames is None or not expected_cols.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=422,
            detail=f"CSV must have columns: {sorted(expected_cols)}. Got: {reader.fieldnames}",
        )

    # Tenancy: every imported row gets the caller's org_id. Compute it
    # once up front, not per-row — same answer either way and one less
    # function call inside a tight loop.
    org_id = new_content_org_id(p)

    created = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        try:
            difficulty = (row.get("difficulty") or "").strip().lower()
            if difficulty not in ALLOWED_DIFFICULTIES:
                errors.append(f"Row {i}: invalid difficulty '{difficulty}'")
                continue
            title = (row.get("title") or "").strip()
            body = row.get("body") or ""
            if not title:
                errors.append(f"Row {i}: title is required")
                continue
            if len(body.split()) < 50:
                errors.append(f"Row {i}: body must be at least 50 words")
                continue
            topic = (row.get("topic") or "").strip() or None

            db.add(Passage(
                title=title, body=body, difficulty=difficulty, topic=topic,
                word_count=len(body.split()),
                organization_id=org_id,
            ))
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")

    db.commit()
    return {"created": created, "errors": errors}


# ============================================================================
# QUESTIONS
# ============================================================================

@router.get("/questions", response_model=List[schemas.QuestionOut])
def list_questions(
    type: Optional[str] = Query(None, alias="type"),
    difficulty: Optional[str] = Query(None),
    passage_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: own-org questions + global. See list_passages."""
    q = tenant_scope_content_read(
        db.query(Question).filter(Question.deleted_at.is_(None)),
        Question,
        p,
    ).order_by(Question.id.desc())
    if type:
        _validate_question_type(type)
        q = q.filter(Question.question_type == type)
    if difficulty:
        _validate_difficulty(difficulty)
        q = q.filter(Question.difficulty == difficulty)
    if passage_id is not None:
        q = q.filter(Question.passage_id == passage_id)
    return q.all()


@router.post("/questions", response_model=schemas.QuestionOut, status_code=201)
def create_question(
    payload: schemas.QuestionCreate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Tenancy: new questions stamped with caller's organization_id.
    For reading_comp questions, the parent passage_id must point to a
    passage that's visible to this principal (own-org or global). If
    the passage isn't visible, we return the same 422 "does not exist"
    as for truly missing IDs — don't leak cross-org passage IDs.
    """
    _validate_difficulty(payload.difficulty)
    _validate_question_type(payload.question_type)
    _validate_options(payload.options, payload.correct_answer)
    if not payload.stem.strip():
        raise HTTPException(status_code=422, detail="stem cannot be empty")

    if payload.question_type == "reading_comp":
        if payload.passage_id is None:
            raise HTTPException(
                status_code=422,
                detail="reading_comp questions must have a passage_id",
            )
        # Use the tenancy-scoped read so cross-org passages are invisible
        # to this caller. Same shape as list_passages.
        passage = tenant_scope_content_read(
            db.query(Passage).filter(Passage.id == payload.passage_id),
            Passage,
            p,
        ).first()
        if not passage:
            raise HTTPException(status_code=422, detail="passage_id does not exist")
    else:
        if payload.passage_id is not None:
            raise HTTPException(
                status_code=422,
                detail=f"{payload.question_type} questions must NOT have a passage_id",
            )

    question = Question(
        passage_id=payload.passage_id,
        question_type=payload.question_type,
        difficulty=payload.difficulty,
        stem=payload.stem.strip(),
        options=payload.options,
        correct_answer=payload.correct_answer,
        organization_id=new_content_org_id(p),
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


@router.patch("/questions/{question_id}", response_model=schemas.QuestionOut)
def update_question(
    question_id: int,
    payload: schemas.QuestionUpdate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: own-org only — 403 on global, 404 on cross-org."""
    question = db.query(Question).filter(Question.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="question not found")
    assert_can_edit_content(question, p)

    if payload.stem is not None:
        if not payload.stem.strip():
            raise HTTPException(status_code=422, detail="stem cannot be empty")
        question.stem = payload.stem.strip()
    if payload.difficulty is not None:
        _validate_difficulty(payload.difficulty)
        question.difficulty = payload.difficulty
    if payload.options is not None or payload.correct_answer is not None:
        new_options = payload.options if payload.options is not None else question.options
        new_correct = payload.correct_answer if payload.correct_answer is not None else question.correct_answer
        _validate_options(new_options, new_correct)
        question.options = new_options
        question.correct_answer = new_correct

    db.commit()
    db.refresh(question)
    return question


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(
    question_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Soft delete — see delete_passage for rationale.
    Tenancy: own-org only."""
    question = db.query(Question).filter(
        Question.id == question_id,
        Question.deleted_at.is_(None),
    ).first()
    if not question:
        raise HTTPException(status_code=404, detail="question not found")
    assert_can_edit_content(question, p)
    question.deleted_at = datetime.utcnow()
    db.commit()
    return None


@router.post("/questions/{question_id}/toggle-disabled", response_model=schemas.QuestionOut)
def toggle_question_disabled(
    question_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Toggle disabled state. See toggle_passage_disabled for rationale.
    Tenancy: own-org only."""
    question = db.query(Question).filter(
        Question.id == question_id,
        Question.deleted_at.is_(None),
    ).first()
    if not question:
        raise HTTPException(status_code=404, detail="question not found")
    assert_can_edit_content(question, p)
    question.disabled_at = None if question.disabled_at else datetime.utcnow()
    db.commit()
    db.refresh(question)
    return question


@router.post("/questions/bulk", response_model=schemas.BulkImportResult)
async def bulk_import_questions(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Bulk-import standalone (non-reading_comp) questions from CSV.
    Expected columns:
        question_type, difficulty, stem, option_a, option_b, option_c, option_d, correct_answer
    correct_answer is the LETTER (A/B/C/D).
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="upload must be a .csv file")

    content = (await file.read()).decode("utf-8")
    reader = csv.DictReader(StringIO(content))
    expected_cols = {"question_type", "difficulty", "stem",
                     "option_a", "option_b", "option_c", "option_d", "correct_answer"}
    if reader.fieldnames is None or not expected_cols.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=422,
            detail=f"CSV must have columns: {sorted(expected_cols)}. Got: {reader.fieldnames}",
        )

    # Tenancy: every imported row gets the caller's org_id.
    org_id = new_content_org_id(p)

    created = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        try:
            qtype = (row.get("question_type") or "").strip().lower()
            if qtype not in ALLOWED_QUESTION_TYPES:
                errors.append(f"Row {i}: invalid question_type '{qtype}'")
                continue
            if qtype == "reading_comp":
                errors.append(f"Row {i}: reading_comp not supported in CSV; use the form")
                continue
            difficulty = (row.get("difficulty") or "").strip().lower()
            if difficulty not in ALLOWED_DIFFICULTIES:
                errors.append(f"Row {i}: invalid difficulty '{difficulty}'")
                continue
            stem = (row.get("stem") or "").strip()
            if not stem:
                errors.append(f"Row {i}: stem is required")
                continue
            options = [
                (row.get("option_a") or "").strip(),
                (row.get("option_b") or "").strip(),
                (row.get("option_c") or "").strip(),
                (row.get("option_d") or "").strip(),
            ]
            if not all(options):
                errors.append(f"Row {i}: all 4 options are required")
                continue
            letter = (row.get("correct_answer") or "").strip().upper()
            if letter not in {"A", "B", "C", "D"}:
                errors.append(f"Row {i}: correct_answer must be A/B/C/D")
                continue
            correct = {"A": 0, "B": 1, "C": 2, "D": 3}[letter]

            db.add(Question(
                passage_id=None,
                question_type=qtype,
                difficulty=difficulty,
                stem=stem,
                options=options,
                correct_answer=correct,
                organization_id=org_id,
            ))
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")

    db.commit()
    return {"created": created, "errors": errors}


# ============================================================================
# WRITING TOPICS
# ============================================================================

@router.get("/writing-topics", response_model=List[schemas.WritingTopicOut])
def list_writing_topics(
    difficulty: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: own-org + global. See list_passages."""
    q = tenant_scope_content_read(
        db.query(WritingTopic).filter(WritingTopic.deleted_at.is_(None)),
        WritingTopic,
        p,
    ).order_by(WritingTopic.id.desc())
    if difficulty:
        _validate_difficulty(difficulty)
        q = q.filter(WritingTopic.difficulty == difficulty)
    return q.all()


@router.post("/writing-topics", response_model=schemas.WritingTopicOut, status_code=201)
def create_writing_topic(
    payload: schemas.WritingTopicCreate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: new writing topic stamped with caller's organization_id."""
    _validate_difficulty(payload.difficulty)
    if not payload.prompt_text.strip():
        raise HTTPException(status_code=422, detail="prompt_text cannot be empty")
    if payload.min_words < 50 or payload.max_words > 1000:
        raise HTTPException(status_code=422, detail="min_words must be ≥ 50 and max_words ≤ 1000")
    if payload.min_words >= payload.max_words:
        raise HTTPException(status_code=422, detail="min_words must be less than max_words")

    topic = WritingTopic(
        prompt_text=payload.prompt_text.strip(),
        difficulty=payload.difficulty,
        min_words=payload.min_words,
        max_words=payload.max_words,
        category=payload.category.strip() if payload.category else None,
        organization_id=new_content_org_id(p),
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.patch("/writing-topics/{topic_id}", response_model=schemas.WritingTopicOut)
def update_writing_topic(
    topic_id: int,
    payload: schemas.WritingTopicUpdate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: own-org only — 403 on global, 404 on cross-org."""
    topic = db.query(WritingTopic).filter(WritingTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="writing topic not found")
    assert_can_edit_content(topic, p)

    if payload.prompt_text is not None:
        if not payload.prompt_text.strip():
            raise HTTPException(status_code=422, detail="prompt_text cannot be empty")
        topic.prompt_text = payload.prompt_text.strip()
    if payload.difficulty is not None:
        _validate_difficulty(payload.difficulty)
        topic.difficulty = payload.difficulty
    if payload.min_words is not None:
        topic.min_words = payload.min_words
    if payload.max_words is not None:
        topic.max_words = payload.max_words
    if payload.category is not None:
        topic.category = payload.category.strip() or None

    if topic.min_words >= topic.max_words:
        raise HTTPException(status_code=422, detail="min_words must be less than max_words")

    db.commit()
    db.refresh(topic)
    return topic


@router.delete("/writing-topics/{topic_id}", status_code=204)
def delete_writing_topic(
    topic_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Soft delete — see delete_passage. Tenancy: own-org only."""
    topic = db.query(WritingTopic).filter(
        WritingTopic.id == topic_id,
        WritingTopic.deleted_at.is_(None),
    ).first()
    if not topic:
        raise HTTPException(status_code=404, detail="writing topic not found")
    assert_can_edit_content(topic, p)
    topic.deleted_at = datetime.utcnow()
    db.commit()
    return None


@router.post("/writing-topics/{topic_id}/toggle-disabled", response_model=schemas.WritingTopicOut)
def toggle_writing_topic_disabled(
    topic_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Toggle disabled state. See toggle_passage_disabled. Tenancy: own-org only."""
    topic = db.query(WritingTopic).filter(
        WritingTopic.id == topic_id,
        WritingTopic.deleted_at.is_(None),
    ).first()
    if not topic:
        raise HTTPException(status_code=404, detail="writing topic not found")
    assert_can_edit_content(topic, p)
    topic.disabled_at = None if topic.disabled_at else datetime.utcnow()
    db.commit()
    db.refresh(topic)
    return topic


@router.post("/writing-topics/bulk", response_model=schemas.BulkImportResult)
async def bulk_import_writing_topics(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Bulk-import writing topics. CSV columns:
        prompt_text, difficulty, min_words, max_words, category
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="upload must be a .csv file")

    content = (await file.read()).decode("utf-8")
    reader = csv.DictReader(StringIO(content))
    expected_cols = {"prompt_text", "difficulty", "min_words", "max_words", "category"}
    if reader.fieldnames is None or not expected_cols.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=422,
            detail=f"CSV must have columns: {sorted(expected_cols)}. Got: {reader.fieldnames}",
        )

    # Tenancy: every imported row gets the caller's org_id.
    org_id = new_content_org_id(p)

    created = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        try:
            difficulty = (row.get("difficulty") or "").strip().lower()
            if difficulty not in ALLOWED_DIFFICULTIES:
                errors.append(f"Row {i}: invalid difficulty '{difficulty}'")
                continue
            prompt_text = (row.get("prompt_text") or "").strip()
            if not prompt_text:
                errors.append(f"Row {i}: prompt_text is required")
                continue
            try:
                min_words = int((row.get("min_words") or "0").strip())
                max_words = int((row.get("max_words") or "0").strip())
            except ValueError:
                errors.append(f"Row {i}: min_words and max_words must be integers")
                continue
            if min_words < 50 or max_words > 1000 or min_words >= max_words:
                errors.append(f"Row {i}: invalid word range (min≥50, max≤1000, min<max)")
                continue
            category = (row.get("category") or "").strip() or None

            db.add(WritingTopic(
                prompt_text=prompt_text, difficulty=difficulty,
                min_words=min_words, max_words=max_words, category=category,
                organization_id=org_id,
            ))
            created += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")

    db.commit()
    return {"created": created, "errors": errors}


# ============================================================================
# SPEAKING TOPICS
# ============================================================================

@router.get("/speaking-topics", response_model=List[schemas.SpeakingTopicOut])
def list_speaking_topics(
    difficulty: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: own-org + global. See list_passages."""
    q = tenant_scope_content_read(
        db.query(SpeakingTopic).filter(SpeakingTopic.deleted_at.is_(None)),
        SpeakingTopic,
        p,
    ).order_by(SpeakingTopic.id.desc())
    if difficulty:
        _validate_difficulty(difficulty)
        q = q.filter(SpeakingTopic.difficulty == difficulty)
    return q.all()


@router.post("/speaking-topics", response_model=schemas.SpeakingTopicOut, status_code=201)
def create_speaking_topic(
    payload: schemas.SpeakingTopicCreate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: stamped with caller's organization_id."""
    _validate_difficulty(payload.difficulty)
    if not payload.prompt_text.strip():
        raise HTTPException(status_code=422, detail="prompt_text cannot be empty")

    topic = SpeakingTopic(
        prompt_text=payload.prompt_text.strip(),
        difficulty=payload.difficulty,
        category=payload.category.strip() if payload.category else None,
        organization_id=new_content_org_id(p),
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.patch("/speaking-topics/{topic_id}", response_model=schemas.SpeakingTopicOut)
def update_speaking_topic(
    topic_id: int,
    payload: schemas.SpeakingTopicUpdate,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Tenancy: own-org only — 403 on global, 404 on cross-org."""
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="speaking topic not found")
    assert_can_edit_content(topic, p)

    if payload.prompt_text is not None:
        if not payload.prompt_text.strip():
            raise HTTPException(status_code=422, detail="prompt_text cannot be empty")
        topic.prompt_text = payload.prompt_text.strip()
    if payload.difficulty is not None:
        _validate_difficulty(payload.difficulty)
        topic.difficulty = payload.difficulty
    if payload.category is not None:
        topic.category = payload.category.strip() or None

    db.commit()
    db.refresh(topic)
    return topic


@router.delete("/speaking-topics/{topic_id}", status_code=204)
def delete_speaking_topic(
    topic_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Soft delete — see delete_passage. Tenancy: own-org only."""
    topic = db.query(SpeakingTopic).filter(
        SpeakingTopic.id == topic_id,
        SpeakingTopic.deleted_at.is_(None),
    ).first()
    if not topic:
        raise HTTPException(status_code=404, detail="speaking topic not found")
    assert_can_edit_content(topic, p)
    topic.deleted_at = datetime.utcnow()
    db.commit()
    return None


@router.post("/speaking-topics/{topic_id}/toggle-disabled", response_model=schemas.SpeakingTopicOut)
def toggle_speaking_topic_disabled(
    topic_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """Toggle disabled state. See toggle_passage_disabled. Tenancy: own-org only."""
    topic = db.query(SpeakingTopic).filter(
        SpeakingTopic.id == topic_id,
        SpeakingTopic.deleted_at.is_(None),
    ).first()
    if not topic:
        raise HTTPException(status_code=404, detail="speaking topic not found")
    assert_can_edit_content(topic, p)
    topic.disabled_at = None if topic.disabled_at else datetime.utcnow()
    db.commit()
    db.refresh(topic)
    return topic


# ============================================================================
# Per-org disable of global content
# ============================================================================
#
# An organization can hide specific GLOBAL passages/questions/topics from
# its own candidates without affecting any other org. The override lives
# in the organization_content_disable table; we look it up at candidate
# content-load time (routes/candidate.py).
#
# WHY this is separate from the existing /toggle-disabled endpoints:
#   - /toggle-disabled flips Content.disabled_at, which is a GLOBAL flag
#     ("nobody sees this row"). It's the right tool for org-private rows.
#   - /disable-for-my-org adds a row in organization_content_disable for
#     ONE org. It's the right tool for global rows that one org wants
#     to hide.
#
# Auth: HR or admin (not super — super has no org, so "for my org" is
# meaningless). Strict so a must_change_password user can't toggle.
# ============================================================================

# URL collection name ('passages', 'questions', etc.) → (SQLAlchemy model,
# stored content_type label). The URL collection matches the existing
# CRUD route naming (plural, kebab-case); the stored label is singular
# snake_case for terse audit/log output.
_CONTENT_TYPE_MAP: dict[str, tuple[type, str]] = {
    "passages": (Passage, "passage"),
    "questions": (Question, "question"),
    "writing-topics": (WritingTopic, "writing_topic"),
    "speaking-topics": (SpeakingTopic, "speaking_topic"),
}


def _resolve_content_type(content_type_url: str) -> tuple[type, str]:
    """Translate the URL segment to (model, content_type label). 422 on unknown."""
    entry = _CONTENT_TYPE_MAP.get(content_type_url)
    if entry is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown content type {content_type_url!r}. "
                f"Expected one of: {sorted(_CONTENT_TYPE_MAP.keys())}."
            ),
        )
    return entry


def _require_org_role(p: Principal) -> int:
    """Per-org-disable is meaningful only for HR/admin (who have an org).
    Super has no organization, so we 403 super here — they should use
    /toggle-disabled (global disable) instead."""
    if p.role == "super" or p.organization_id is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Per-org disable is for HR/admin only. Super-admin should "
                "use the global /toggle-disabled endpoint for content-wide changes."
            ),
        )
    return p.organization_id


@router.post("/{content_type_url}/{content_id}/disable-for-my-org", status_code=200)
def disable_content_for_my_org(
    content_type_url: str,
    content_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Hide a GLOBAL content row from the caller's org's candidates.

    Returns 200 if the row is now disabled-for-this-org (idempotent —
    calling twice is fine, same result).

    422 if:
      - content_type isn't one of the four supported types
      - the row is org-private (use /toggle-disabled instead)
    404 if the row doesn't exist OR isn't visible to the caller
        (cross-tenant — same generic message either way).
    """
    org_id = _require_org_role(p)
    model, content_type_label = _resolve_content_type(content_type_url)

    row = db.query(model).filter(
        model.id == content_id,
        model.deleted_at.is_(None),
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="content not found")

    # Tenancy: caller must be able to see this row. HR/admin see global
    # + own-org. Anything else is 404.
    if row.organization_id is not None and row.organization_id != org_id:
        raise HTTPException(status_code=404, detail="content not found")

    # Org-private rows go through the existing global toggle, not this one.
    if row.organization_id is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "This row belongs to your organization. Use /toggle-disabled "
                "instead, which globally toggles its disabled_at column."
            ),
        )

    # Idempotent insert. Check first so duplicate-key doesn't generate a
    # noisy IntegrityError in the logs.
    existing = (
        db.query(OrganizationContentDisable)
        .filter(
            OrganizationContentDisable.organization_id == org_id,
            OrganizationContentDisable.content_type == content_type_label,
            OrganizationContentDisable.content_id == content_id,
        )
        .first()
    )
    if existing is None:
        db.add(OrganizationContentDisable(
            organization_id=org_id,
            content_type=content_type_label,
            content_id=content_id,
            disabled_by=p.user.id,
        ))
        db.commit()

    return {"status": "disabled_for_org", "organization_id": org_id,
            "content_type": content_type_label, "content_id": content_id}


@router.post("/{content_type_url}/{content_id}/enable-for-my-org", status_code=200)
def enable_content_for_my_org(
    content_type_url: str,
    content_id: int,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    Un-hide a previously-hidden global content row for the caller's org.
    Idempotent — calling on something that was never disabled is fine.
    """
    org_id = _require_org_role(p)
    _model, content_type_label = _resolve_content_type(content_type_url)

    deleted = (
        db.query(OrganizationContentDisable)
        .filter(
            OrganizationContentDisable.organization_id == org_id,
            OrganizationContentDisable.content_type == content_type_label,
            OrganizationContentDisable.content_id == content_id,
        )
        .delete()
    )
    if deleted:
        db.commit()
    return {"status": "enabled_for_org", "organization_id": org_id,
            "content_type": content_type_label, "content_id": content_id}


@router.get("/disabled-for-my-org")
def list_disabled_for_my_org(
    db: Session = Depends(get_db),
    p: Principal = Depends(require_principal(allow=("super", "admin", "hr"), strict=True)),
):
    """
    The IDs of global content rows the caller's org has hidden, grouped
    by content type. Frontend reads this once at page load to mark
    matching rows in its manage-questions list with the off-toggle state.

    Returns the four buckets even when empty so the frontend doesn't
    need to special-case missing keys.
    """
    org_id = _require_org_role(p)

    rows = (
        db.query(OrganizationContentDisable)
        .filter(OrganizationContentDisable.organization_id == org_id)
        .all()
    )

    bucket: dict[str, list[int]] = {
        "passage": [],
        "question": [],
        "writing_topic": [],
        "speaking_topic": [],
    }
    for r in rows:
        bucket.setdefault(r.content_type, []).append(r.content_id)
    for key in bucket:
        bucket[key].sort()
    return bucket