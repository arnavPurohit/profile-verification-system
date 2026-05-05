from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..fetchers import AccountChallengedError, FetcherError, FetcherUnavailableError
from ..services import CompanyService, ProfileService


class FetchProfileBody(BaseModel):
    urn_or_url: str
    max_age_days: int | None = None


class FetchCompanyBody(BaseModel):
    urn: str


def build(profile_svc: ProfileService, company_svc: CompanyService) -> APIRouter:
    router = APIRouter()

    @router.post("/fetch/profile")
    async def fetch_profile(body: FetchProfileBody):
        try:
            profile, source = await profile_svc.get_or_fetch(
                body.urn_or_url, max_age_days=body.max_age_days,
            )
        except FetcherUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except AccountChallengedError as e:
            raise HTTPException(status_code=503, detail=f"account challenged: {e}")
        except FetcherError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"source": source, "profile": profile.model_dump(mode="json")}

    @router.post("/fetch/company")
    async def fetch_company(body: FetchCompanyBody):
        try:
            company, source = await company_svc.get_or_fetch(body.urn)
        except FetcherUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except FetcherError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"source": source, "company": company.model_dump(mode="json")}

    return router
