"""Unit tests for orchestrator/coordinator.py — mocked Anthropic client, no network."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.coordinator import CoordinatorIntent, interpret_prompt


def _mock_client(tool_input: dict) -> MagicMock:
    client = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_final_answer"
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    client.messages.create = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_interpret_prompt_specific_mode():
    client = _mock_client({
        "mode": "specific", "tickers": ["NVDA", "AMD"],
        "priority_sectors": [], "excluded_sectors": [], "focus": "confronto diretto NVDA vs AMD",
    })
    intent = await interpret_prompt(client, "confrontami NVDA e AMD", [])
    assert isinstance(intent, CoordinatorIntent)
    assert intent.mode == "specific"
    assert intent.tickers == ["NVDA", "AMD"]
    assert intent.focus == "confronto diretto NVDA vs AMD"


@pytest.mark.asyncio
async def test_interpret_prompt_discovery_mode():
    client = _mock_client({
        "mode": "discovery", "tickers": [],
        "priority_sectors": ["Banking"], "excluded_sectors": [],
        "focus": "settore bancario europeo",
    })
    intent = await interpret_prompt(client, "opportunità nel settore bancario europeo ora", [])
    assert intent.mode == "discovery"
    assert intent.tickers == []
    assert intent.priority_sectors == ["Banking"]


@pytest.mark.asyncio
async def test_interpret_prompt_missing_optional_fields_default_empty():
    client = _mock_client({"mode": "discovery", "tickers": None, "priority_sectors": None,
                            "excluded_sectors": None, "focus": None})
    intent = await interpret_prompt(client, "cosa mi consigli?", [])
    assert intent.tickers == []
    assert intent.priority_sectors == []
    assert intent.excluded_sectors == []
    assert intent.focus == ""


@pytest.mark.asyncio
async def test_interpret_prompt_passes_history_into_prompt():
    client = _mock_client({
        "mode": "specific", "tickers": ["NVDA"],
        "priority_sectors": [], "excluded_sectors": [], "focus": "approfondimento NVDA",
    })
    history = [
        {"role": "user", "content": "confrontami NVDA e AMD"},
        {"role": "assistant", "content": "..."},
    ]
    await interpret_prompt(client, "approfondisci il primo", history)

    call_kwargs = client.messages.create.call_args.kwargs
    user_message = call_kwargs["messages"][0]["content"]
    assert "confrontami NVDA e AMD" in user_message
