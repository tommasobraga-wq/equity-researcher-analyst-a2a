"""Generic ReAct tool-use loop built directly on the Anthropic SDK.

Replaces per-agent frameworks (OpenAI Agents SDK, Smolagents, BeeAI ReActAgent)
with a single hand-rolled loop: call the model with `tools=[...]`, execute any
`tool_use` blocks the model requests, feed results back as `tool_result` blocks,
repeat until the model stops requesting tools (or `max_iterations` is hit).
"""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import anthropic


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Awaitable[str]]


_OUTPUT_TOOL_NAME = "submit_final_answer"


async def run_react(
    client: anthropic.AsyncAnthropic,
    system: str,
    user_prompt: str,
    tools: list[ToolSpec],
    model: str,
    max_tokens: int = 4096,
    max_iterations: int = 10,
    output_schema: dict[str, Any] | None = None,
) -> str | dict[str, Any]:
    """Runs the tool-use loop.

    If `output_schema` is given, an extra "submit_final_answer" tool carrying
    that schema is added and `tool_choice` is forced to "any" on every turn —
    the model can never emit free-form prose, only regular tool calls or a
    final call to `submit_final_answer` matching the schema. This is far more
    token-economical and reliable than asking the model to "output only JSON,
    no prose" in the prompt: instead of parsing/repairing text, we get back an
    already-schema-shaped dict (`response.content[i].input`) and the loop
    returns that dict directly. Without `output_schema`, behaves as a plain
    ReAct loop and returns the final text response.
    """
    tool_defs = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    if output_schema is not None:
        tool_defs.append({
            "name": _OUTPUT_TOOL_NAME,
            "description": (
                "Call this exactly once, when your analysis is complete, to submit "
                "the final structured result. Do not emit any other text."
            ),
            "input_schema": output_schema,
        })

    # Prompt caching: `system` and `tools` are byte-identical across every retry of
    # the same stage (the LangGraph validation-retry loop resends the same node with
    # only the user-turn feedback changed) and across every iteration of this same
    # loop. Marking the last block of each as an ephemeral cache breakpoint means the
    # (often large) instructions + schema prefix is billed once and reused. Harmless
    # no-op on models/accounts without caching support (e.g. currently Haiku on this
    # account) — it's simply ignored, so this is safe to leave on unconditionally.
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if tool_defs:
        tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}

    handlers = {t.name: t.handler for t in tools}
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]

    for _ in range(max_iterations):
        kwargs: dict[str, Any] = dict(
            model=model, max_tokens=max_tokens, system=system_blocks, messages=messages, tools=tool_defs,
            # Extended thinking is on by default for some models (e.g. Sonnet 5) even
            # without requesting it, and those thinking tokens are billed as output —
            # we never use the thinking content (only the tool_use/text result), so
            # disable it to avoid paying for reasoning we discard.
            thinking={"type": "disabled"},
        )
        if output_schema is not None:
            kwargs["tool_choice"] = {"type": "any"}
        response = await client.messages.create(**kwargs)

        if output_schema is not None:
            for block in response.content:
                if block.type == "tool_use" and block.name == _OUTPUT_TOOL_NAME:
                    return block.input

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name != _OUTPUT_TOOL_NAME:
                result_text = await handlers[block.name](**block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"ReAct loop exceeded max_iterations={max_iterations} without a final answer.")
