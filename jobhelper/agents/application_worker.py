from __future__ import annotations

#import webbrowser - using playwright for agent to act on browser activity to submit the application on the company's career sites
import asyncio
import json
import logging
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError
from pathlib import pathlib
from typing import Dict, Any

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

logger = logging.getLogger("jobhelper.application_worker")

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
                    # Non-blocking background task execution
                    asyncio.create_task(self._run_playwright_apply(app.id, job.url))
                except Exception as exc:
                    logger.error(f"Failed to spawn Playwright task: {exc}")

            return app
            

# This function will automatically apply for the job that was approved
async def _run_playwright_apply(self, app_id: int, url: str) -> None:
        """Launches Chromium via Playwright, navigates to career site, and executes ATS-specific automation."""
        profile_path = Path("data/candidate_profile.json")
        if not profile_path.exists():
            # Fallback check for root directory profile path
            profile_path = Path("candidate_profile.json")

        profile = {}
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        else:
            logger.warning(f"[Playwright App #{app_id}] Profile file not found. Proceeding with empty profile.")

        async with async_playwright() as p:
            # Launch Chromium in headful mode so you can watch the agent run live
            browser = await p.chromium.launch(headless=False, slow_mo=50)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                logger.info(f"[Playwright App #{app_id}] Navigating to {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)

                # Route dynamically based on URL pattern or DOM indicators
                if "workday" in url.lower() or await page.locator("[data-automation-id]").count() > 0:
                    await self._handle_workday_apply(page, profile)
                elif "greenhouse.io" in url.lower() or await page.locator("#greenhouse-application-form").count() > 0:
                    await self._handle_greenhouse_apply(page, profile)
                else:
                    await self._handle_generic_apply(page, profile)

                logger.info(f"[Playwright App #{app_id}] Completed automation sequence for {url}")
                await asyncio.sleep(5)  # Keep window open briefly for inspection

            except PlaywrightTimeoutError:
                logger.error(f"[Playwright App #{app_id}] Timeout navigating to {url}")
            except Exception as exc:
                logger.error(f"[Playwright Error App #{app_id}] Failed during auto-apply: {exc}", exc_info=True)
            finally:
                await browser.close()

      async def _handle_greenhouse_apply(self, page: Page, profile: Dict[str, Any]) -> None:
        """Fills out standard Greenhouse application forms."""
        logger.info("[Playwright] Executing Greenhouse ATS handler...")
        
        # Candidate basic info
        if await page.locator("input#first_name").is_visible():
            await page.fill("input#first_name", profile.get("first_name", ""))
            await page.fill("input#last_name", profile.get("last_name", ""))
            await page.fill("input#email", profile.get("email", ""))
            await page.fill("input#phone", profile.get("phone", ""))

        # Upload Resume
        resume_input = page.locator("input[type='file'][autocomplete='off'], input[type='file']#resume_upload")
        resume_path = profile.get("resume_pdf_path", "data/resume.pdf")
        if await resume_input.count() > 0 and Path(resume_path).exists():
            await resume_input.first.set_input_files(resume_path)
            logger.info(f"[Playwright] Attached resume from {resume_path}")

        # Social Links
        if await page.locator("input[autocomplete='custom-question-linkedin-profile']").is_visible():
            await page.fill("input[autocomplete='custom-question-linkedin-profile']", profile.get("linkedin_url", ""))

    async def _handle_workday_apply(self, page: Page, profile: Dict[str, Any]) -> None:
        """Handles Workday multi-step application entry points."""
        logger.info("[Playwright] Executing Workday ATS handler...")
        
        apply_btn = page.locator("[data-automation-id='applyButton']")
        if await apply_btn.is_visible():
            await apply_btn.click()
            await page.wait_for_timeout(2000)

        manual_apply = page.locator("[data-automation-id='applyManually']")
        if await manual_apply.is_visible():
            await manual_apply.click()
            await page.wait_for_timeout(2000)

        resume_input = page.locator("[data-automation-id='file-upload-drop-zone'] input[type='file']")
        resume_path = profile.get("resume_pdf_path", "data/resume.pdf")
        if await resume_input.count() > 0 and Path(resume_path).exists():
            await resume_input.set_input_files(resume_path)
            logger.info(f"[Playwright] Uploaded resume to Workday: {resume_path}")

    async def _handle_generic_apply(self, page: Page, profile: Dict[str, Any]) -> None:
        """Generic fallback for standard input names."""
        logger.info("[Playwright] Executing generic form filler...")
        field_mappings = [
            ("input[name*='first' i], input[id*='first' i]", profile.get("first_name")),
            ("input[name*='last' i], input[id*='last' i]", profile.get("last_name")),
            ("input[type='email'], input[name*='email' i]", profile.get("email")),
            ("input[type='tel'], input[name*='phone' i]", profile.get("phone")),
        ]
        for selector, val in field_mappings:
            if val and await page.locator(selector).count() > 0:
                try:
                    await page.locator(selector).first.fill(val)
                except Exception:
                    pass          