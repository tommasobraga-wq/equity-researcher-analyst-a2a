"""Smoke tests — nessuna chiamata LLM, solo infrastruttura HTTP/A2A.

Verificano che ogni agente sia raggiungibile, risponda con la struttura
corretta su /health e /.well-known/agent.json, e rifiuti metodi sconosciuti.
"""
import pytest
import httpx
from conftest import AGENTS, base_url, a2a_payload


# ── /health ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug,meta", AGENTS.items())
def test_health(http: httpx.Client, slug: str, meta: dict):
    r = http.get(f"{base_url(meta['port'])}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["agent"] == meta["name"]


# ── /.well-known/agent.json ───────────────────────────────────────────────────

@pytest.mark.parametrize("slug,meta", AGENTS.items())
def test_agent_card(http: httpx.Client, slug: str, meta: dict):
    r = http.get(f"{base_url(meta['port'])}/.well-known/agent.json")
    assert r.status_code == 200
    card = r.json()
    for field in ("name", "version", "description", "url", "capabilities"):
        assert field in card, f"Campo mancante in agent card: {field}"
    assert isinstance(card["capabilities"], list)
    assert len(card["capabilities"]) > 0


# ── /tasks — errori di protocollo ────────────────────────────────────────────

@pytest.mark.parametrize("slug,meta", AGENTS.items())
def test_tasks_wrong_method(http: httpx.Client, slug: str, meta: dict):
    payload = a2a_payload("ciao")
    payload["method"] = "tasks/unknown"
    r = http.post(f"{base_url(meta['port'])}/tasks", json=payload)
    assert r.status_code == 404
    body = r.json()
    assert body["error"] is not None
    assert body["error"]["code"] == -32601


@pytest.mark.parametrize("slug,meta", AGENTS.items())
def test_tasks_invalid_params(http: httpx.Client, slug: str, meta: dict):
    r = http.post(
        f"{base_url(meta['port'])}/tasks",
        json={"jsonrpc": "2.0", "method": "tasks/send", "id": 1, "params": {"bad": "data"}},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] is not None
    assert body["error"]["code"] == -32602
