from __future__ import annotations

#import webbrowser - using playwright for agent to act on browser activity to submit the application on the company's career sites
import asyncio
import json
import logging
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError
from pathlib import Path
from typing import Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobhelper.config import settings
from jobhelper.db import SessionLocal
from jobhelper.llm import generate, generate_json, LLMError
from jobhelper.models import Application, Job
from jobhelper.store import (
    application_for_job,
    create_pending,
    in_progress_count,
    log_event,
    touch,
)

from jobhelper.mcp_browser import MCPApplicationRunner

logger = logging.getLogger("jobhelper.application_worker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)

class ApplicationWorker:
    """Surfaces matches for approval when fewer than MAX_IN_PROGRESS applications are active."""
    def __init__(self):
        self.mcp_runner = MCPApplicationRunner()

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
        """Handles human approval or rejection of an application."""
        from jobhelper.db import SessionLocal
        async with SessionLocal() as session:
            app = await session.get(Application, application_id)
            if not app:
                raise ValueError(f"Application {application_id} not found")
            
            if approved:
                app.status = "approved"
                await session.commit()
            else:
                app.status = "rejected"
                await session.commit()
                return app

        # Trigger the application process asynchronously or directly
        if approved:
            asyncio.create_task(self.process_application(application_id))
        
        return app
            
    # This function will automatically apply for the job that was approved
    async def _resolve_career_site_url(self, company_name: str, job_title: str, max_retries: int = 3) -> str:
        """Uses Gemini first; falls back to a local Ollama model if Gemini hits rate limits."""
        prompt = f"""
        You are a job search routing assistant. 
        For the company "{company_name}" hiring for "{job_title}", what is the direct URL to their job board, openings page, or ATS portal (such as Greenhouse, Workday, Lever, or a direct /jobs or /careers/openings listing page)?
        
        DO NOT return a generic marketing landing page splash screen that requires clicking "See Open Positions". Return the deepest direct URL for job listings if possible.
        
        Provide ONLY the raw URL starting with https://. Do not include markdown or commentary.
        """
        
        # 1. Try Gemini first with retries
        backoff_seconds = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[URL Resolver] Trying Gemini (Attempt {attempt}/{max_retries}) for {company_name}...")
                url = await generate(prompt, system="Output valid URLs only.")
                url = url.strip().strip("`'\"")
                if url.startswith("http") and "google.com" not in url:
                    return url
            except Exception as e:
                logger.warning(f"[URL Resolver] Gemini attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2

        # 2. Fallback to Local LLM (Ollama) if Gemini fails
        logger.warning(f"[URL Resolver] Gemini unavailable. Falling back to local Ollama LLM for {company_name}...")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload = {
                    "model": "qwen2.5:7b",  # Or whatever model you pulled
                    "prompt": f"Output ONLY the direct career site URL starting with https:// for {company_name} hiring {job_title}. No markdown, no commentary.\nURL:",
                    "stream": False,
                    "options": {"temperature": 0.1}
                }
                response = await client.post("http://localhost:11434/api/generate", json=payload)
                if response.status_code == 200:
                    data = response.json()
                    local_text = data.get("response", "").strip().strip("`'\"")
                    # Extract first valid http URL from local response
                    for word in local_text.split():
                        if word.startswith("http") and "google.com" not in word:
                            logger.info(f"[URL Resolver] Local LLM successfully resolved URL: {word}")
                            return word
        except Exception as local_err:
            logger.error(f"[URL Resolver] Local LLM fallback failed: {local_err}")

        # 3. Final safe fallback (Guessed domain pattern, NOT Google Search)
        clean_company = company_name.lower().replace(" ", "").replace(",", "").replace(".", "")
        fallback_url = f"https://www.{clean_company}.com/careers"
        logger.warning(f"[URL Resolver] All AI resolvers failed. Using guessed fallback: {fallback_url}")
        return fallback_url

    async def _run_playwright_apply(self, app_id: int, url: str) -> None:
        """Autonomous LLM-driven loop that dynamically navigates and applies to the job."""
        profile_path = Path("data/candidate_profile.json")
        if not profile_path.exists():
            profile_path = Path("candidate_profile.json")

        profile = {}
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)

        # Retrieve job and candidate metadata from DB
        async with SessionLocal() as session:
            app = await session.get(Application, app_id)
            job = await session.get(Job, app.job_id) if app else None

        if not job:
            logger.error(f"[Playwright App #{app_id}] Job record not found.")
            return

        company_name = getattr(job, "company", "Unknown Company")
        job_title = getattr(job, "title", "Software Engineer")
        job_id = getattr(job, "external_id", "") or str(job.id)

        logger.info(f"[Playwright App #{app_id}] Starting dynamic LLM worker for: {company_name} | {job_title}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, slow_mo=100)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                # 1. Resolve career portal URL
                target_url = await self._resolve_career_site_url(company_name, job_title)
                logger.info(f"[Playwright App #{app_id}] Navigating to resolved portal: {target_url}")
                
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)

                # 2. Run the dynamic LLM inspection & action loop
                await self._run_dynamic_llm_loop(page, profile, company_name, job_title, job_id)

                logger.info(f"[Playwright App #{app_id}] Application loop finished successfully.")
                await asyncio.sleep(10)

            except Exception as exc:
                logger.error(f"[Playwright Error App #{app_id}] Dynamic application failed: {exc}", exc_info=True)
            finally:
                await browser.close()

    async def process_application(self, application_id: int):
        """Fetches job and profile details, then executes application via Playwright MCP."""
        logger.info("Get Application and Job Details from DB")
        # 1. Get Application & Job details from DB
        async with SessionLocal() as session:
            app = await session.get(Application, application_id)
            if not app:
                logger.error(f"Application {application_id} not found.")
                return

            job = await session.get(Job, app.job_id)
            if not job:
                logger.error(f"Job {app.job_id} not found.")
                return

            job_title = job.title
            company_name = job.company
            job_id = job.id

            logger.info(f"Successfully retreived Company Name and Job Title: '{company_name}' and '{job_title}'")

            # Mark status as in_progress
            app.status = "in_progress"
            await session.commit()
        
        # 2. Load candidate profile
        profile = self._load_candidate_profile()        
        logger.info("Successfully loaded candidate profile.  Now resolving company career site")
        # 3. Resolve Company career website so that playwright mcp can apply at the company website instead of Job board site.
        try:
            target_url = await self._resolve_career_site_url(company_name, job_title)
            logger.info(f"[ApplicationWorker] Resolved direct portal '{target_url}' for {job_title} at {company_name}")

            logger.info(f"[ApplicationWorker] Starting MCP-driven application for {job.title} at {job.company} ({job.url})")
            
            # 4. Run application using Playwright MCP
            await self.mcp_runner.run_application(
                target_url=target_url,
                job_title=job.title,
                company_name=job.company,
                candidate_profile=profile
            )

            # 5. Mark as applied upon successful completion
            async with SessionLocal() as session:
                app = await session.get(Application, application_id)
                if app:
                    app.status = "applied"
                    await session.commit()
                logger.info(f"[ApplicationWorker] Successfully completed application for job ID {job.id}")

        except Exception as e:
            logger.error(f"[ApplicationWorker] Failed application flow for job {job.id}: {e}")
            app.status = "failed"
            await session.commit()

    def _load_candidate_profile(self) -> dict:
        import json
        try:
            with open("data/candidate_profile.json", "r") as f:
                return json.load(f)
        except Exception:
            return {"name": "Alexander Gyoungmin Yi", "email": "alex@example.com"}