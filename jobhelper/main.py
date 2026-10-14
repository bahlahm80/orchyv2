import uvicorn

from orchy.api import create_app
from orchy.config import settings

app = create_app()


def run() -> None:
    uvicorn.run(
        "orchy.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
