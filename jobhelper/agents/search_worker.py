from __future__ import annotations

import httpx

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
            # Adding logic to make sure duplicate or already seen or applied jobs don't show up on the list
            existing_result = await session.execute(select(Job.external_id))
            existing_ids = {row[0] for row in existing_result.all() if row[0]}

            # Only post new listings
            new_postings = [p for p in postings if p.external_id not in existing_ids]
            total_new = len(new_postings)

            # Batch configuration - because I'm using free version, gemini api has limit on the usage
            # So we will need to run the match analysis via LLM on certain number of postings
            # We will start with 5 postings at once and repeat every 5 minutes.
            BATCH_SIZE = 5
            BATCH_DELAY_SECONDS = 300
            total_batches = (total_new + BATCH_SIZE - 1) // BATCH_SIZE

            for batch_num, i in enumerate(range(0, total_new, BATCH_SIZE), start = 1):
                batch = new_postings[i : i + BATCH_SIZE]
                batch_stored = 0
                batch_matched = 0

                for posting in batch:
                    if posting.external_id in existing_ids:
                        continue

                    result = await score_job(
                        resume,
                        title=posting.title,
                        company=posting.company,
                        location=posting.location,
                        description=posting.description or posting.title,
                        threshold=settings.match_threshold,
                    )
                    # skip anything lower than threshold but in the beginning we will start with high threshold just to make sure
                    if result.score < settings.match_threshold:
                        existing_ids.add(posting.external_id)
                        continue

                    await upsert_job(session, posting, result.score, result.reason, result.matched)
                    existing_ids.add(posting.external_id)
                    stored += 1
                    batch_stored += 1
                    if result.matched:
                        matched += 1
                        batch_matched += 1

                await log_event(
                    session,
                    "search_worker",
                    f"Batch {batch_num}/{total_batches} complete. Stored {batch_stored} relevant roles "
                    f"({batch_matched} pending approval).",
                )
                await session.commit()

                # If there are still more jobs left to process, pause to reset Gemini
                if i + BATCH_SIZE < total_new:
                    print(f"[Search Worker] Batch complete.  Sleeping {BATCH_DELAY_SECONDS}s to refresh Gemini API quota...")
                    await asyncio.sleep(BATCH_DELAY_SECONDS)
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
        except Exception as exc:  # LinkedIn guest HTML is brittle; But keeping samples off.  If want to test, change the mode in .env to sample
            self._last_error = str(exc)
            return [], "linkedin"
        return jobs, "linkedin"

    async def ingest_url(self, url: str) -> dict:
        """Manually fetch, score, and store a single job posting by URL."""
        resume = load_resume()
        
        from jobhelper.sources.linkedin import _job_id, fetch_job_details
        from jobhelper.sources.base import JobPosting
        from jobhelper.models import Application

        job_id = _job_id(url)
        if not job_id:
            raise ValueError("Could not parse a valid LinkedIn job ID from URL.")

        # 1. Fetch metadata via httpx
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JobHelper/0.1"}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
            details = await fetch_job_details(client, job_id)

        posting = JobPosting(
            external_id=job_id,
            title=str(details.get("title", "Software Engineer")),
            company=str(details.get("company", "LinkedIn")),
            location=str(details.get("location", "US")),
            url=str(details.get("url", f"https://www.linkedin.com/jobs/view/{job_id}")),
            description=str(details.get("description", "")),
            source="linkedin",
        )

        # 2. Score job with Gemini
        result = await score_job(
            resume,
            title=posting.title,
            company=posting.company,
            location=posting.location,
            description=str(posting.description or posting.title),
            threshold=settings.match_threshold,
        )

        if result.score < settings.match_threshold:
            return {
                "title": posting.title,
                "company": posting.company,
                "score": result.score,
                "matched": False,
                "reason": f"Filtered out: score {result.score:.2f} is below threshold {settings.match_threshold:.2f}.",
            }

        # 3. Store in SQLite & create pending application if matched
        async with SessionLocal() as session:
            db_job = await upsert_job(session, posting, result.score, result.reason, result.matched)
            
            if result.matched:
                existing_app = (
                    await session.scalars(select(Application).where(Application.job_id == db_job.id))
                ).first()
                if not existing_app:
                    new_app = Application(
                        job_id=db_job.id,
                        status="pending_approval",
                        notes=f"Match score {result.score:.2f} passed threshold {settings.match_threshold}."
                    )
                    session.add(new_app)

            await log_event(
                session,
                "search_worker",
                f"Ingested job '{posting.title}' at {posting.company}. Score: {result.score:.2f}",
            )
            await session.commit()

        return {
            "title": posting.title,
            "company": posting.company,
            "score": result.score,
            "matched": result.matched,
            "reason": result.reason,
        }