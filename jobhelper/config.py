from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    resume_path: Path = ROOT / "data" / "resume.md"
    max_in_progress: int = 5
    match_threshold: float = 0.72
    job_source: str = "auto"
    linkedin_keywords: str = "software engineer python"
    linkedin_location: str = "United States"
    linkedin_geo_id: str = ""
    search_interval_seconds: int = 180
    manager_tick_seconds: int = 15
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./data/jobhelper.db"


settings = Settings()


def load_resume() -> str:
    path = settings.resume_path
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Resume not found at {path}")
    return path.read_text(encoding="utf-8").strip()
