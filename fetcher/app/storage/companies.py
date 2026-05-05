from __future__ import annotations

from typing import Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..domain import Company


class CompaniesRepoProtocol(Protocol):
    async def get(self, urn: str) -> Company | None: ...
    async def find_by_normalized(self, normalized: str) -> Company | None: ...
    async def search(self, query: str, limit: int = 5) -> list[Company]: ...
    async def upsert(self, company: Company) -> None: ...


class CompaniesRepo:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db.companies

    async def get(self, urn: str) -> Company | None:
        doc = await self._col.find_one({"urn": urn})
        if not doc:
            return None
        doc.pop("_id", None)
        return Company.model_validate(doc)

    async def find_by_normalized(self, normalized: str) -> Company | None:
        doc = await self._col.find_one({
            "$or": [{"normalized": normalized}, {"aliases": normalized}]
        })
        if not doc:
            return None
        doc.pop("_id", None)
        return Company.model_validate(doc)

    async def search(self, query: str, limit: int = 5) -> list[Company]:
        cursor = self._col.find(
            {"$text": {"$search": query}},
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)
        results: list[Company] = []
        async for doc in cursor:
            doc.pop("_id", None)
            doc.pop("score", None)
            results.append(Company.model_validate(doc))
        return results

    async def upsert(self, company: Company) -> None:
        await self._col.update_one(
            {"urn": company.urn},
            {"$set": company.model_dump(mode="json")},
            upsert=True,
        )
