"""Extension intake endpoint.

The Chrome extension hooks chrome.webRequest.onCompleted, captures Voyager
XHR responses, and POSTs them here. We parse + upsert as if the user's
own browser had done the fetch — because, technically, it did.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import structlog

from ..parsers import parse_company, parse_profile
from ..storage import CapturesRepo, CompaniesRepo, ProfilesRepo

log = structlog.get_logger(__name__)


class CaptureBody(BaseModel):
    kind: str  # 'profile' | 'company'
    url: str
    payload: dict[str, Any]


def build(
    profiles: ProfilesRepo,
    companies: CompaniesRepo,
    captures: CapturesRepo,
    *,
    parser_version: str = "1.0.0",
) -> APIRouter:
    router = APIRouter()

    @router.post("/captures")
    async def receive_capture(body: CaptureBody):
        now = datetime.now(timezone.utc)
        try:
            if body.kind == "profile":
                profile = parse_profile(
                    body.payload, parser_version=parser_version,
                    source="extension", fetched_at=now,
                )
                await captures.insert(
                    urn=profile.urn, kind="profile", payload=body.payload, source="extension",
                )
                await profiles.upsert(profile)
                log.info("capture.profile", urn=profile.urn)
                return {"ok": True, "urn": profile.urn}

            if body.kind == "company":
                company = parse_company(
                    body.payload, parser_version=parser_version,
                    source="extension", fetched_at=now,
                )
                await captures.insert(
                    urn=company.urn, kind="company", payload=body.payload, source="extension",
                )
                await companies.upsert(company)
                log.info("capture.company", urn=company.urn)
                return {"ok": True, "urn": company.urn}

            raise HTTPException(status_code=400, detail=f"unknown kind: {body.kind}")
        except Exception as e:
            log.error("capture.parse_failed", error=str(e), kind=body.kind)
            raise HTTPException(status_code=400, detail=f"parse failed: {e}")

    return router
