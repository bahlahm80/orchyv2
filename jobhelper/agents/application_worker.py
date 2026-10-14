from __future__ import annotations

#import webbrowser - using playwright for agent to act on browser activity to submit the application on the company's career sites
import asyncio
import json
from playwright.async_api import async_playwright

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobhelper.config import settings
from jobhelper.db import SessionLocal
from jobhelper.models import Application, Job
from jobhelper.store import (
    application_for_job,
    create_pending,
    in_progress_count,
    log_event,
    touch,
)


class ApplicationWorker:
    """Surfaces matches for approval when fewer than MAX_IN_PROGRESS applications are active."""

    async def run_once(self) -> dict:
        async with SessionLocal() as session:
            active = await in_progress_count(session)
            queued = 0
            if active < settings.max_in_progress:
                queued = await self._queue_matches(session, settings.max_in_progress - active)
                if queued:
                    await log_event(
                        session,
                        "application_worker",
                        f"{active} in progress (< {settings.max_in_progress}). Queued {queued} job(s) for approval.",
                    )
            else:
                await log_event(
                    session,
                    "application_worker",
                    f"{active} applications already in progress; not asking for new approvals.",
                )
            await session.commit()
            return {"in_progress": active, "queued": queued}

    async def _queue_matches(self, session: AsyncSession, slots: int) -> int:
        jobs = (
            await session.scalars(
                select(Job).where(Job.matched == 1).order_by(Job.match_score.desc(), Job.id.asc())
            )
        ).all()
        queued = 0
        for job in jobs:
            if queued >= slots:
                break
            existing = await application_for_job(session, job.id)
            if existing:
                continue
            await create_pending(session, job)
            queued += 1
        return queued

    async def decide(self, application_id: int, approved: bool) -> Application:
        async with SessionLocal() as session:
            app = await session.get(Application, application_id)
            if not app:
                raise ValueError(f"Application {application_id} not found")
            if app.status != "pending_approval":
                raise ValueError(f"Application {application_id} is {app.status}, not pending_approval")
            if not approved:
                app.status = "declined"
                app.notes = "User disapproved. No apply action taken."
                touch(app)
                await log_event(session, "application_worker", f"User declined application #{app.id}.")
                await session.commit()
                await session.refresh(app)
                return app

            # Job apply approved.  Apply to thejob by going to the company career website
            job = await session.get(Job, app.job_id)
            app.status = "in_progress"
            app.notes = (
                "Application approved.  Playwright agent will be laucnhed to navigate targetted company's career website."
            )
            # app.apply_url = job.url if job else app.apply_url
            touch(app)

            await log_event(
                session,
                "application_worker",
                f"User approved '{job.title if job else app.id}'. Launching Playwright browser worker to apply...",
            )
            await session.commit()
            await session.refresh(app)

            if job and job.url:
                try:
                    asyncio.create_task(self._rub_playwright_apply(app.id, job.url))
                    return app
                except Exception:
                    pass
            

# This function will automatically apply for the job that was approved
async def _run_playwright_apply(self, app_id: int, url: str) -> None:
        """Launches Chromium via Playwright, navigates to the career site, and fills out form fields."""
        with open("candidate_profile.json", "r") as f:
            profile = json.load(f)

        async with async_playwright() as p:
            # Launch Chromium (set headless=False so you can see the agent working)
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Basic form fill logic (e.g. standard inputs)
                # We can add site-specific handlers for Workday, Greenhouse, or Lever here
                print(f"[Playwright] Navigated to {url}")
                
                # Keep browser window active for inspection/automation testing
                await asyncio.sleep(10)
                
            except Exception as exc:
                print(f"[Playwright Error] Failed during auto-apply: {exc}")
            finally:
                await browser.close()