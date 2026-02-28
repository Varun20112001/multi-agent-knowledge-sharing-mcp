from fastapi.testclient import TestClient

from app.main import app


def test_api_key_required_for_ingest() -> None:
    client = TestClient(app)
    response = client.post("/v1/ingest/repo", json={"project_id": "00000000-0000-0000-0000-000000000000", "repo_path": "."})
    assert response.status_code == 401


def test_mcp_info_available() -> None:
    client = TestClient(app)
    response = client.get("/v1/mcp/info")
    assert response.status_code == 200
    payload = response.json()
    assert "ensure_project" in payload["tools"]
    assert "search_docs" in payload["tools"]
