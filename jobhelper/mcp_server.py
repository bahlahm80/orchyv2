"""Stdio MCP server exposing Orchy job tools to other local agents."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from orchy.agents.application_worker import ApplicationWorker
from orchy.db import SessionLocal, init_db
from orchy.models import Application
from orchy.store import dashboard_rows, in_progress_count, recent_events

mcp = MCPServer("orchy-jobs")
worker = ApplicationWorker()


@mcp.tool()
async def list_applications() -> list[dict]:
    """Return the job application dashboard rows."""
    await init_db()
    async with SessionLocal() as session:
        return await dashboard_rows(session)


@mcp.tool()
async def in_progress_applications() -> dict:
    """Count applications currently in progress."""
    await init_db()
    async with SessionLocal() as session:
        return {"in_progress": await in_progress_count(session)}


@mcp.tool()
async def approve_application(application_id: int) -> dict:
    """Approve submitting an application after human intent is confirmed."""
    await init_db()
    app = await worker.decide(application_id, approved=True)
    return {"id": app.id, "status": app.status}


@mcp.tool()
async def reject_application(application_id: int) -> dict:
    """Disapprove a pending application. No apply action is taken."""
    await init_db()
    app = await worker.decide(application_id, approved=False)
    return {"id": app.id, "status": app.status}


@mcp.tool()
async def pending_approvals() -> list[dict]:
    """List applications waiting on the user."""
    await init_db()
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(Application).where(Application.status == "pending_approval"))
        ).all()
        return [{"id": r.id, "job_id": r.job_id, "status": r.status} for r in rows]


@mcp.tool()
async def agent_log() -> list[dict]:
    """Recent manager and worker events."""
    await init_db()
    async with SessionLocal() as session:
        return await recent_events(session)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
