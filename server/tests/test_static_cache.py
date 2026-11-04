from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.main import ApplicationStaticFiles


def test_entry_and_service_worker_revalidate_but_hashed_assets_keep_default(tmp_path):
    (tmp_path / "index.html").write_text('<script src="/assets/index-abc123.js"></script>')
    (tmp_path / "sw.js").write_text("self.addEventListener('install', () => {})")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log('version')")

    app = FastAPI()
    app.mount("/", ApplicationStaticFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        for path in ("/", "/index.html", "/sw.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
        asset = client.get("/assets/index-abc123.js")
        assert asset.status_code == 200
        assert "cache-control" not in asset.headers
