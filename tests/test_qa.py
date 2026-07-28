"""Unit tests for shared/qa.py — verdict parsing and the run_llm_qa wrapper (mocked client)."""
from unittest.mock import MagicMock

from shared.qa import parse_qa_verdict, run_llm_qa


def test_parse_qa_verdict_approvato():
    approved, corrections = parse_qa_verdict("QA: APPROVATO\nTutto corretto.")
    assert approved is True
    assert corrections == []


def test_parse_qa_verdict_da_correggere():
    approved, _ = parse_qa_verdict("QA: DA_CORREGGERE\nManca la citazione.")
    assert approved is False


def test_parse_qa_verdict_tolerates_bracket_echo():
    approved, _ = parse_qa_verdict("QA: [APPROVATO]\nOk.")
    assert approved is True


def test_parse_qa_verdict_no_match_is_not_approved():
    approved, _ = parse_qa_verdict("Risposta inattesa senza verdetto.")
    assert approved is False


def test_parse_qa_verdict_extracts_corrections():
    text = (
        "QA: DA_CORREGGERE\nErrore nello scoring.\n"
        '=== CORREZIONI ===\n[{"ticker": "AAPL", "field": "scoring.totale", "value": 31, "motivo": "somma errata"}]'
    )
    approved, corrections = parse_qa_verdict(text)
    assert approved is False
    assert corrections == [{"ticker": "AAPL", "field": "scoring.totale", "value": 31, "motivo": "somma errata"}]


def test_parse_qa_verdict_malformed_corrections_json_is_ignored():
    text = "QA: DA_CORREGGERE\n=== CORREZIONI ===\n[{not valid json}]"
    approved, corrections = parse_qa_verdict(text)
    assert approved is False
    assert corrections == []


def _mock_client(reply_text: str) -> MagicMock:
    client = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = reply_text
    response = MagicMock()
    response.content = [block]
    client.messages.create.return_value = response
    return client


def test_run_llm_qa_approved():
    client = _mock_client("QA: APPROVATO\nOk.")
    approved, raw = run_llm_qa(client, "system prompt", "subject json", correlation_id="test", agent="test_agent")
    assert approved is True
    assert "APPROVATO" in raw


def test_run_llm_qa_rejected():
    client = _mock_client("QA: DA_CORREGGERE\nProblema trovato.")
    approved, raw = run_llm_qa(client, "system prompt", "subject json", correlation_id="test", agent="test_agent")
    assert approved is False


def test_run_llm_qa_skips_non_text_blocks():
    client = MagicMock()
    thinking_block = MagicMock()
    thinking_block.type = "thinking"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "QA: APPROVATO"
    response = MagicMock()
    response.content = [thinking_block, text_block]
    client.messages.create.return_value = response

    approved, raw = run_llm_qa(client, "system", "subject", correlation_id="test", agent="test_agent")
    assert approved is True
    assert raw == "QA: APPROVATO"
