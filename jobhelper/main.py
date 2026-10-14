import uvicorn

from jobhelper.api import create_app
from jobhelper.config import settings

app = create_app()


def run() -> None:
    uvicorn.run(
        "jobhelper.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
