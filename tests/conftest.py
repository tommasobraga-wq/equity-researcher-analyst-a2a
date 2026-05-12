"""Shared fixtures for A2A agent tests."""
import uuid
import pytest
import httpx

AGENTS = {
    "data-collector":      {"port": 8001, "name": "DataCollector"},
    "news-sentiment":      {"port": 8002, "name": "NewsSentiment"},
    "fundamental-analyst": {"port": 8003, "name": "FundamentalAnalyst"},
    "risk-assessor":       {"port": 8004, "name": "RiskAssessor"},
    "report-writer":       {"port": 8005, "name": "ReportWriter"},
}


def base_url(port: int) -> str:
    return f"http://localhost:{port}"


def a2a_payload(text: str, task_id: str | None = None) -> dict:
    """Build a minimal valid JSON-RPC tasks/send payload."""
    return {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "id": 1,
        "params": {
            "id": task_id or str(uuid.uuid4()),
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": text}],
            },
        },
    }


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    with httpx.Client(timeout=120.0) as client:
        yield client
