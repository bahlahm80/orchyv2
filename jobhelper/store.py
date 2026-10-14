from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchy.models import (
    IN_PROGRESS_STATUSES,
    AgentEvent,
    Application,
    ChatMessage,
    Job,
)
from orchy.sources.base import JobPosting


async def log_event(session: AsyncSession, actor: str, message: str) -> AgentEvent:
    event = AgentEvent(actor=actor, message=message)
    session.add(event)
    await session.flush()
    return event


async def in_progress_count(session: AsyncSession) -> int:
    result = await session.scalar(
        select(func.count()).select_from(Application).where(
            Application.status.in_(IN_PROGRESS_STATUSES)
        )
    )
    return int(result or 0)


async def upsert_job(session: AsyncSession, posting: JobPosting, score: float, reason: str, matched: bool) -> Job:
    existing = await session.scalar(
        select(Job).where(Job.source == posting.source, Job.external_id == posting.external_id)
    )
    if existing:
        existing.title = posting.title
        existing.company = posting.company
        existing.location = posting.location
        existing.url = posting.url
        if posting.description:
            existing.description = posting.description
        existing.match_score = score
        existing.match_reason = reason
        existing.matched = int(matched)
        return existing
    job = Job(
        source=posting.source,
        external_id=posting.external_id,
        title=posting.title,
        company=posting.company,
        location=posting.location,
        url=posting.url,
        description=posting.description,
        match_score=score,
        match_reason=reason,
        matched=int(matched),
    )
    session.add(job)
    await session.flush()
    return job


async def application_for_job(session: AsyncSession, job_id: int) -> Application | None:
    return await session.scalar(select(Application).where(Application.job_id == job_id))


async def create_pending(session: AsyncSession, job: Job) -> Application:
    app = Application(
        job_id=job.id,
        status="pending_approval",
        apply_url=job.url,
        notes="Waiting for user approval before apply.",
    )
    session.add(app)
    await session.flush()
    return app


async def dashboard_rows(session: AsyncSession) -> list[dict]:
    jobs = (await session.scalars(select(Job).order_by(Job.created_at.desc()))).all()
    apps = (await session.scalars(select(Application))).all()
    app_by_job = {a.job_id: a for a in apps}
    rows = []
    for job in jobs:
        app = app_by_job.get(job.id)
        rows.append(
            {
                "job_id": job.id,
                "application_id": app.id if app else None,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.url,
                "source": job.source,
                "match_score": job.match_score,
                "match_reason": job.match_reason,
                "matched": bool(job.matched),
                "status": app.status if app else "discovered",
                "notes": app.notes if app else "",
                "updated_at": (app.updated_at if app else job.created_at).isoformat(),
            }
        )
    return rows


async def recent_events(session: AsyncSession, limit: int = 40) -> list[dict]:
    rows = (
        await session.scalars(select(AgentEvent).order_by(AgentEvent.id.desc()).limit(limit))
    ).all()
    return [
        {"id": e.id, "actor": e.actor, "message": e.message, "created_at": e.created_at.isoformat()}
        for e in rows
    ]


async def chat_history(session: AsyncSession, limit: int = 50) -> list[dict]:
    rows = (
        await session.scalars(select(ChatMessage).order_by(ChatMessage.id.asc()).limit(limit))
    ).all()
    return [
        {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
        for m in rows
    ]


def touch(app: Application) -> None:
    app.updated_at = datetime.now(timezone.utc)
