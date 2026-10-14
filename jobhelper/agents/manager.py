from __future__ import annotations

import asyncio
from time import monotonic

from sqlalchemy import select

from jobhelper.agents.application_worker import ApplicationWorker
from jobhelper.agents.search_worker import SearchWorker
from jobhelper.config import load_resume, settings
from jobhelper.db import SessionLocal
from jobhelper.llm import LLMError, generate
from jobhelper.models import Application, ChatMessage, Job
from jobhelper.store import dashboard_rows, in_progress_count, log_event


class ManagerAgent:
    """Coordinates workers on a tick, then answers chat with live dashboard context."""

    def __init__(self) -> None:
        self.search = SearchWorker()
        self.applications = ApplicationWorker()
        self._running = False
        self.last_cycle: dict = {}

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._loop(), name="orchy-manager")

    async def stop(self) -> None:
        self._running = False

    async def _loop(self) -> None:
        last_search = 0.0
        while self._running:
            try:
                now = monotonic()
                if now - last_search >= settings.search_interval_seconds:
                    search_result = await self.search.run_once()
                    last_search = now
                else:
                    search_result = {"skipped": True}
                apply_result = await self.applications.run_once()
                self.last_cycle = {"search": search_result, "applications": apply_result}
                async with SessionLocal() as session:
                    await log_event(
                        session,
                        "manager",
                        "Cycle complete. Next search in "
                        f"{settings.search_interval_seconds}s; application gate "
                        f"{apply_result.get('in_progress')}/{settings.max_in_progress}.",
                    )
                    await session.commit()
            except Exception as exc:
                async with SessionLocal() as session:
                    await log_event(session, "manager", f"Cycle failed: {exc}")
                    await session.commit()
            await asyncio.sleep(settings.manager_tick_seconds)

    async def chat(self, user_text: str) -> str:
        async with SessionLocal() as session:
            session.add(ChatMessage(role="user", content=user_text))
            resume = load_resume()
            rows = await dashboard_rows(session)
            active = await in_progress_count(session)
            pending = (
                await session.scalars(select(Application).where(Application.status == "pending_approval"))
            ).all()
            jobs = (await session.scalars(select(Job).order_by(Job.match_score.desc()).limit(8))).all()
            snapshot = {
                "in_progress": active,
                "max_in_progress": settings.max_in_progress,
                "pending_approvals": [
                    {"id": a.id, "job_id": a.job_id, "status": a.status} for a in pending
                ],
                "jobs": [
                    {
                        "id": j.id,
                        "title": j.title,
                        "company": j.company,
                        "score": j.match_score,
                        "matched": bool(j.matched),
                    }
                    for j in jobs
                ],
                "dashboard": rows[:12],
                "last_cycle": self.last_cycle,
            }
            system = (
                "You are the Orchy manager agent. You coordinate a LinkedIn search worker and an "
                "application worker. Never claim you submitted an Easy Apply form. Approvals happen "
                "in the UI. Be concise. Use the resume as ground truth for fit."
            )
            prompt = (
                f"RESUME:\n{resume}\n\nSTATE:\n{snapshot}\n\nUSER:\n{user_text}\n"
            )
            try:
                reply = await generate(prompt, system=system)
            except LLMError as exc:
                reply = (
                    f"I can still report local state without Gemini: {active} applications in progress, "
                    f"{len(pending)} waiting on approval. LLM error: {exc}"
                )
            session.add(ChatMessage(role="assistant", content=reply))
            await log_event(session, "manager", "Responded to user chat.")
            await session.commit()
            return reply
