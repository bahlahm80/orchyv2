from __future__ import annotations

from jobhelper.config import load_resume, settings
from jobhelper.db import SessionLocal
from jobhelper.matching import score_job
from jobhelper.sources.linkedin import LinkedInJobSource
from jobhelper.sources.sample import SampleJobSource
from jobhelper.store import log_event, upsert_job


class SearchWorker:
    """Continuously searches LinkedIn (or sample listings) and scores against the resume."""

    def __init__(self) -> None:
        self._last_error = ""

    async def run_once(self) -> dict:
        resume = load_resume()
        postings, source_name = await self._fetch()
        matched = 0
        stored = 0
        async with SessionLocal() as session:
            for posting in postings:
                result = await score_job(
                    resume,
                    title=posting.title,
                    company=posting.company,
                    location=posting.location,
                    description=posting.description or posting.title,
                    threshold=settings.match_threshold,
                )
                await upsert_job(session, posting, result.score, result.reason, result.matched)
                stored += 1
                if result.matched:
                    matched += 1
            await log_event(
                session,
                "search_worker",
                f"Scanned {stored} {source_name} jobs; {matched} passed guardrails "
                f"(threshold {settings.match_threshold}).",
            )
            await session.commit()
        return {"source": source_name, "scanned": stored, "matched": matched}

    async def _fetch(self):
        mode = settings.job_source.lower()
        if mode == "sample":
            return await SampleJobSource().search(settings.linkedin_keywords, settings.linkedin_location), "sample"
        try:
            jobs = await LinkedInJobSource(settings.linkedin_geo_id).search(
                settings.linkedin_keywords, settings.linkedin_location
            )
            if jobs:
                return jobs, "linkedin"
            if mode == "linkedin":
                return [], "linkedin"
        except Exception as exc:  # LinkedIn guest HTML is brittle; keep local loop alive.
            self._last_error = str(exc)
            if mode == "linkedin":
                raise
        jobs = await SampleJobSource().search(settings.linkedin_keywords, settings.linkedin_location)
        return jobs, "sample"
