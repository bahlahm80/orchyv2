from __future__ import annotations

import webbrowser

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

            job = await session.get(Job, app.job_id)
            app.status = "applied"
            app.notes = (
                "Recorded as applied after approval. Complete the LinkedIn form in the browser if it is still open."
            )
            app.apply_url = job.url if job else app.apply_url
            touch(app)
            if job and job.url:
                try:
                    webbrowser.open(job.url)
                except Exception:
                    pass
            await log_event(
                session,
                "application_worker",
                f"User approved. Marked '{job.title if job else app.id}' as applied and opened the listing.",
            )
            await session.commit()
            await session.refresh(app)
            return app
