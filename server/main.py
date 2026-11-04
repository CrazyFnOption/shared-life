"""Single-process local entry point; no accounts are created on startup."""

import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from server.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]


class ApplicationStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in {"", ".", "index.html", "sw.js"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


def application():
    db_path = Path(os.environ.get("SHARED_EXPENSE_DB", ROOT / ".local/dev.sqlite3"))
    origins = os.environ.get(
        "SHARED_EXPENSE_ORIGINS", "http://127.0.0.1:8765,http://localhost:8765"
    ).split(",")
    app = create_app(
        db_path,
        start_worker=True,
        allowed_origins=[origin.strip() for origin in origins if origin.strip()],
        secure_cookies=os.environ.get("SHARED_EXPENSE_SECURE_COOKIE") == "1",
    )
    web_dist = ROOT / "web/dist"
    if web_dist.is_dir():
        app.mount("/", ApplicationStaticFiles(directory=web_dist, html=True), name="web")
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:application",
        factory=True,
        host="127.0.0.1",
        port=int(os.environ.get("SHARED_EXPENSE_PORT", "8765")),
        access_log=False,
    )
