from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import CompanyService


class SearchBody(BaseModel):
    query: str
    limit: int = 5


def build(company_svc: CompanyService) -> APIRouter:
    router = APIRouter()

    @router.post("/search/company")
    async def search_company(body: SearchBody):
        results = await company_svc.search(body.query, limit=body.limit)
        return {"results": [c.model_dump(mode="json") for c in results]}

    return router
