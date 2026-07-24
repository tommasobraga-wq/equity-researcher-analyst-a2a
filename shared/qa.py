"""Shared LLM-QA mechanism, generalized from ReportWriter's internal QA pass.

Each agent supplies its own tailored system prompt; the call/parse
mechanics (verdict line + optional corrections block) are shared here.
"""
import json
import re

import anthropic

_VERDICT_RE = re.compile(r"QA:\s*\[?(APPROVATO|DA_CORREGGERE)\]?", re.IGNORECASE)


def parse_qa_verdict(text: str) -> tuple[bool, list[dict]]:
    """Returns (approved, corrections). APPROVATO -> True, DA_CORREGGERE -> False.

    Note: the verdict keyword was originally "CORRETTO", which is ambiguous in
    Italian (readable as "it is correct" instead of the intended "needs
    correcting") — models were misusing it to mean approval, causing false
    rejections. "DA_CORREGGERE" ("to be corrected") is unambiguous.

    The `\\]?` tolerance handles models that echo the prompt's own
    "[APPROVATO|DA_CORREGGERE]" alternation notation literally (e.g.
    "QA: [APPROVATO]") instead of picking one bare word as intended.
    """
    match = _VERDICT_RE.search(text)
    approved = bool(match) and match.group(1).upper() == "APPROVATO"

    corrections: list[dict] = []
    marker = "=== CORREZIONI ==="
    idx = text.find(marker)
    if idx != -1:
        after = text[idx + len(marker):].strip()
        start = after.find("[")
        end = after.rfind("]") + 1
        if start != -1 and end > start:
            try:
                corrections = json.loads(after[start:end])
            except json.JSONDecodeError:
                corrections = []

    return approved, corrections


def run_llm_qa(
    client: anthropic.Anthropic,
    system_prompt: str,
    subject_json: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 1024,
) -> tuple[bool, str]:
    """Calls Claude to QA-review `subject_json`. Returns (approved, raw_verdict_text)."""
    system_blocks = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": subject_json}],
        # Some models (e.g. Sonnet 5) enable extended thinking by default and bill
        # those tokens as output even though we only ever use the verdict text.
        thinking={"type": "disabled"},
    )
    # Models with extended thinking enabled (e.g. Sonnet 5) may prepend a
    # ThinkingBlock before the text response — content[0] is not always text.
    raw = "".join(block.text for block in response.content if block.type == "text")
    approved, _ = parse_qa_verdict(raw)
    return approved, raw
