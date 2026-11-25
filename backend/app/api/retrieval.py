from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, StringConstraints

from app.core.security import current_user
from app.services import retrieval

router = APIRouter(prefix='/api/v1', tags=['资料检索'])
User = Annotated[dict, Depends(current_user)]


class SearchPayload(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    request_key: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


@router.post('/courses/{course_id}/search')
def submit(course_id: int, payload: SearchPayload, user: User, response: Response):
    result = retrieval.submit(user, course_id, payload.question, payload.request_key)
    if result['status'] == 'PROCESSING': response.status_code = 202
    return result


@router.get('/searches/{request_id}')
def get_search(request_id: int, user: User):
    return retrieval.get_request(user, request_id)
