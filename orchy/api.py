from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from orchy.agents.manager import ManagerAgent
from orchy.config import ROOT, settings
from orchy.db import SessionLocal, init_db
from orchy.models import Application
from orchy.store import chat_history, dashboard_rows, in_progress_count, recent_events, touch

manager = ManagerAgent()
WEB = ROOT / "web"


class ChatIn(BaseModel):
    message: str


class StatusIn(BaseModel):
    status: str


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await init_db()
        await manager.start()
        yield
        await manager.stop()

    app = FastAPI(title="Orchy v2", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "model": settings.gemini_model}

    @app.get("/api/state")
    async def state() -> dict:
        async with SessionLocal() as session:
            return {
                "in_progress": await in_progress_count(session),
                "max_in_progress": settings.max_in_progress,
                "threshold": settings.match_threshold,
                "keywords": settings.linkedin_keywords,
                "location": settings.linkedin_location,
                "source_mode": settings.job_source,
                "has_gemini_key": bool(settings.gemini_api_key),
                "model": settings.gemini_model,
                "jobs": await dashboard_rows(session),
                "events": await recent_events(session),
                "chat": await chat_history(session),
                "last_cycle": manager.last_cycle,
            }

    @app.post("/api/applications/{application_id}/approve")
    async def approve(application_id: int) -> dict:
        try:
            app_row = await manager.applications.decide(application_id, approved=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": app_row.id, "status": app_row.status, "notes": app_row.notes}

    @app.post("/api/applications/{application_id}/reject")
    async def reject(application_id: int) -> dict:
        try:
            app_row = await manager.applications.decide(application_id, approved=False)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": app_row.id, "status": app_row.status, "notes": app_row.notes}

    @app.post("/api/applications/{application_id}/status")
    async def set_status(application_id: int, body: StatusIn) -> dict:
        allowed = {
            "pending_approval",
            "applied",
            "screening",
            "interviewing",
            "rejected",
            "withdrawn",
            "offer",
            "declined",
        }
        if body.status not in allowed:
            raise HTTPException(400, f"Unknown status {body.status}")
        async with SessionLocal() as session:
            app_row = await session.get(Application, application_id)
            if not app_row:
                raise HTTPException(404, "Application not found")
            app_row.status = body.status
            app_row.notes = f"Status updated from dashboard to {body.status}."
            touch(app_row)
            await session.commit()
            return {"id": app_row.id, "status": app_row.status}

    @app.post("/api/search/now")
    async def search_now() -> dict:
        return await manager.search.run_once()

    @app.post("/api/chat")
    async def chat(body: ChatIn) -> dict:
        reply = await manager.chat(body.message.strip())
        return {"reply": reply}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                async with SessionLocal() as session:
                    pending = (
                        await session.scalars(
                            select(Application).where(Application.status == "pending_approval")
                        )
                    ).all()
                    await socket.send_json(
                        {
                            "in_progress": await in_progress_count(session),
                            "pending": len(list(pending)),
                            "jobs": await dashboard_rows(session),
                            "events": await recent_events(session, 12),
                        }
                    )
                await socket.receive_text()
        except WebSocketDisconnect:
            return

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
