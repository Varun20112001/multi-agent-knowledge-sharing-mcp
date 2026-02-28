import os

import httpx

BASE = os.getenv("APP_BASE", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "dev-local-key")
PROJECT_ID = os.getenv("PROJECT_ID")
REPO_PATH = os.getenv("REPO_PATH", ".")

if not PROJECT_ID:
    raise SystemExit("Set PROJECT_ID environment variable.")

headers = {"x-api-key": API_KEY}

with httpx.Client(base_url=BASE, headers=headers, timeout=30) as client:
    ingest = client.post(
        "/v1/ingest/repo",
        json={"project_id": PROJECT_ID, "repo_path": REPO_PATH},
    )
    ingest.raise_for_status()
    print("ingest:", ingest.json())

    info = client.get("/v1/mcp/info")
    info.raise_for_status()
    print("mcp:", info.json())
