"""Danh sách tài liệu, kiểm tra bài mới, kiểm tra lại (spec web UI §7.3)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from citeproof.core.enums import RunStatus, StageName, Verifier
from citeproof.core.errors import CiteProofError, ConflictError, InputError, RateLimitedError
from citeproof.core.settings import Settings
from citeproof.services import deletion, documents
from citeproof.services.documents import ADMIN_VARIANTS, DemoConfig
from citeproof.web.deps import (
    AppSettings,
    AppStorage,
    ConsentedUser,
    Csrf,
    CurrentUser,
    Db,
    UserDep,
    rollback_keeping_user,
)
from citeproof.web.rendering import render

router = APIRouter()

TOASTS = {"deleted": "Đã xóa bài"}
FINISHED = {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}
MAX_DEMO_DELAY = 5.0


def _status_code(exc: CiteProofError) -> int:
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, RateLimitedError):
        return 429
    if exc.code == "FILE_TOO_LARGE":
        return 413
    return 400


def _variant(current: CurrentUser, value: str | None) -> Verifier:
    if current.is_admin and value in {v.value for v in ADMIN_VARIANTS}:
        return Verifier(value)
    return documents.DEFAULT_VARIANT


def _demo(settings: Settings, delay: str | None, fail_at: str | None) -> DemoConfig | None:
    if settings.env != "local":
        return None
    try:
        seconds = min(max(float(delay or 0), 0.0), MAX_DEMO_DELAY)
    except ValueError:
        seconds = 0.0
    stage = StageName(fail_at) if fail_at in {s.value for s in StageName} else None
    if seconds == 0 and stage is None:
        return None
    return DemoConfig(step_delay_s=seconds, fail_at=stage)


def _new_page(
    request: Request,
    current: CurrentUser,
    *,
    mode: str,
    status: int = 200,
    error: str | None = None,
    form: dict[str, Any] | None = None,
) -> Response:
    return render(
        request,
        "pages/new.html",
        {
            "mode": "text" if mode == "text" else "file",
            "error": error,
            "form": form or {},
            "variants": [v.value for v in ADMIN_VARIANTS],
            "stages": list(StageName),
        },
        current=current,
        nav="new",
        status=status,
    )


@router.get("/documents")
async def documents_page(
    request: Request, db: Db, current: UserDep, done: str | None = None
) -> Response:
    rows = await documents.list_documents(db, user=current.user)
    return render(
        request,
        "pages/documents.html",
        {"rows": rows, "toast": TOASTS.get(done or ""), "finished": FINISHED},
        current=current,
        nav="documents",
    )


@router.get("/documents/new")
async def new_page(request: Request, current: ConsentedUser, mode: str = "file") -> Response:
    return _new_page(request, current, mode=mode)


@router.post("/documents", dependencies=[Csrf])
async def upload_document(
    request: Request,
    db: Db,
    storage: AppStorage,
    settings: AppSettings,
    current: ConsentedUser,
    file: Annotated[UploadFile | None, File()] = None,
    variant: Annotated[str | None, Form()] = None,
    demo_delay: Annotated[str | None, Form()] = None,
    demo_fail_at: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        if file is None:
            raise InputError("Hãy chọn một tệp PDF hoặc DOCX.", code="EMPTY_FILE")
        data = await documents.read_upload(file, settings.max_upload_bytes)
        document, run_id = await documents.create_file_document(
            db,
            storage,
            settings,
            user=current.user,
            filename=file.filename,
            data=data,
            variant=_variant(current, variant),
            demo=_demo(settings, demo_delay, demo_fail_at),
        )
    except (InputError, ConflictError, RateLimitedError) as exc:
        await rollback_keeping_user(db, current)
        return _new_page(request, current, mode="file", status=_status_code(exc), error=str(exc))
    try:
        await db.commit()
    except Exception:
        await storage.delete(document.storage_key)
        raise
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@router.post("/documents/text", dependencies=[Csrf])
async def submit_text(
    request: Request,
    db: Db,
    storage: AppStorage,
    settings: AppSettings,
    current: ConsentedUser,
    title: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    references: Annotated[str, Form()] = "",
    variant: Annotated[str | None, Form()] = None,
    demo_delay: Annotated[str | None, Form()] = None,
    demo_fail_at: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        document, run_id = await documents.create_text_document(
            db,
            storage,
            settings,
            user=current.user,
            title=title,
            body=body,
            references=references,
            variant=_variant(current, variant),
            demo=_demo(settings, demo_delay, demo_fail_at),
        )
    except (InputError, ConflictError, RateLimitedError) as exc:
        await rollback_keeping_user(db, current)
        form = {"title": title, "body": body, "references": references}
        return _new_page(
            request, current, mode="text", status=_status_code(exc), error=str(exc), form=form
        )
    try:
        await db.commit()
    except Exception:
        await storage.delete(document.storage_key)
        raise
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@router.post("/documents/{document_id}/runs", dependencies=[Csrf])
async def rerun(
    db: Db,
    settings: AppSettings,
    current: ConsentedUser,
    document_id: UUID,
    variant: Annotated[str | None, Form()] = None,
) -> Response:
    document = await documents.get_own_document(db, user=current.user, document_id=document_id)
    run_id = await documents.start_run(
        db, settings, user=current.user, document=document, variant=_variant(current, variant)
    )
    await db.commit()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@router.get("/documents/{document_id}/delete")
async def delete_confirm(request: Request, db: Db, current: UserDep, document_id: UUID) -> Response:
    document = await documents.get_own_document(db, user=current.user, document_id=document_id)
    return render(
        request,
        "pages/delete_document.html",
        {"document": document, "is_sample": documents.is_sample(document)},
        current=current,
        nav="documents",
    )


@router.post("/documents/{document_id}/delete", dependencies=[Csrf])
async def delete_submit(
    db: Db, storage: AppStorage, current: UserDep, document_id: UUID
) -> Response:
    await deletion.delete_document(db, storage, user=current.user, document_id=document_id)
    return RedirectResponse("/documents?done=deleted", status_code=303)
