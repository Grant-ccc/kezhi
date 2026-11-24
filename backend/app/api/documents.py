from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field, StringConstraints

from app.core.config import get_settings
from app.core.errors import BusinessError
from app.core.security import current_user
from app.services import documents

router = APIRouter(prefix="/api/v1", tags=["资料"])
User = Annotated[dict, Depends(current_user)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


def read_upload(upload: UploadFile):
    try:
        return upload.file.read(get_settings().max_upload_bytes + 1)
    finally:
        upload.file.close()


@router.get("/documents")
def document_list(user: User, course_id: int | None = Query(default=None, ge=1), page: int = Query(default=1, ge=1, le=10000), q: str = Query(default="", max_length=120), kind: Literal["TEACHING", "NOTE"] | None = None):
    return documents.list_documents(user, course_id=course_id, page=page, query=q.strip(), kind=kind)


@router.post("/courses/{course_id}/documents", status_code=201)
def document_upload(course_id: int, user: User, title: Annotated[Title, Form()], request_key: Annotated[str, Form(min_length=1, max_length=64)], file: Annotated[UploadFile, File()]):
    return documents.save_document(user, course_id, title, file.filename or "", read_upload(file), request_key)


@router.put("/documents/{document_id}/file")
def document_replace(document_id: int, user: User, title: Annotated[Title, Form()], expected_version: Annotated[int, Form(ge=1)], request_key: Annotated[str, Form(min_length=1, max_length=64)], file: Annotated[UploadFile, File()]):
    document = documents.get_document(user, document_id)
    return documents.save_document(user, document["course_id"], title, file.filename or "", read_upload(file), request_key, document_id=document_id, expected_version=expected_version)


@router.get("/documents/{document_id}")
def document_detail(document_id: int, user: User):
    return documents.get_document(user, document_id)


@router.get("/documents/{document_id}/download")
def document_download(document_id: int, user: User):
    document, data = documents.read_file(user, document_id)
    return Response(content=data, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(document['original_filename'], safe='')}"})


@router.get("/documents/{document_id}/preview")
def document_preview(document_id: int, user: User):
    document, data = documents.read_file(user, document_id)
    if document["media_type"] == "application/pdf":
        raise BusinessError(415, "PREVIEW_UNAVAILABLE", "PDF 请下载后查看，文本预览将在资料解析阶段扩展")
    return {"title": document["title"], "content": data.decode("utf-8-sig"), "content_version": document["content_version"]}


class RenamePayload(BaseModel):
    title: Title


@router.patch("/documents/{document_id}")
def document_rename(document_id: int, payload: RenamePayload, user: User):
    return documents.rename_document(user, document_id, payload.title)


@router.delete("/documents/{document_id}")
def document_delete(document_id: int, user: User):
    return documents.delete_document(user, document_id)


class RetryPayload(BaseModel):
    expected_version: int = Field(strict=True, ge=1)
    expected_retry_round: int = Field(strict=True, ge=0)


@router.post("/documents/{document_id}/retry")
def document_retry(document_id: int, payload: RetryPayload, user: User):
    return documents.retry_document(user, document_id, payload.expected_version, payload.expected_retry_round)


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: int, user: User, page: int = Query(default=1, ge=1, le=10000)):
    return documents.chunk_preview(user, document_id, page)
