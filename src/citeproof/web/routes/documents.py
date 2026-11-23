"""Danh sách tài liệu và kiểm tra bài mới (bản tạm, hoàn thiện ở Task 6)."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from citeproof.web.deps import ConsentedUser, UserDep
from citeproof.web.rendering import render

router = APIRouter()


@router.get("/documents")
async def documents_page(request: Request, current: UserDep) -> Response:
    return render(request, "pages/documents.html", {"rows": []}, current=current, nav="documents")


@router.get("/documents/new")
async def new_page(request: Request, current: ConsentedUser) -> Response:
    return render(request, "pages/documents.html", {"rows": []}, current=current, nav="new")
