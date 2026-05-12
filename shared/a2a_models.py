"""A2A protocol models — JSON-RPC 2.0 over HTTP."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ------------------------------------------------------------------ #
# Agent Card                                                           #
# ------------------------------------------------------------------ #

class AgentCard(BaseModel):
    name: str
    version: str
    description: str
    url: str
    capabilities: list[str]
    input_schema: dict[str, Any] = {}


# ------------------------------------------------------------------ #
# Message parts                                                        #
# ------------------------------------------------------------------ #

class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class DataPart(BaseModel):
    type: Literal["data"] = "data"
    data: dict[str, Any]


MessagePart = TextPart | DataPart


class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: list[MessagePart]

    def text(self) -> str:
        return " ".join(p.text for p in self.parts if isinstance(p, TextPart))


# ------------------------------------------------------------------ #
# Task (request)                                                       #
# ------------------------------------------------------------------ #

class A2ATask(BaseModel):
    id: str
    message: Message
    metadata: dict[str, Any] = {}


# ------------------------------------------------------------------ #
# Task Result (response)                                               #
# ------------------------------------------------------------------ #

class A2ATaskResult(BaseModel):
    id: str
    status: Literal["completed", "failed", "working"]
    message: Message
    metadata: dict[str, Any] = {}

    @classmethod
    def ok(cls, task_id: str, text: str, data: dict[str, Any] | None = None) -> "A2ATaskResult":
        parts: list[MessagePart] = [TextPart(text=text)]
        if data:
            parts.append(DataPart(data=data))
        return cls(id=task_id, status="completed", message=Message(role="agent", parts=parts))

    @classmethod
    def fail(cls, task_id: str, error: str) -> "A2ATaskResult":
        return cls(
            id=task_id,
            status="failed",
            message=Message(role="agent", parts=[TextPart(text=error)]),
        )


# ------------------------------------------------------------------ #
# JSON-RPC 2.0 envelope                                                #
# ------------------------------------------------------------------ #

class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any]
    id: int | str = 1


class JsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    id: int | str = 1

    @classmethod
    def ok(cls, result: dict[str, Any], rpc_id: int | str = 1) -> "JsonRpcResponse":
        return cls(result=result, id=rpc_id)

    @classmethod
    def fail(cls, code: int, message: str, rpc_id: int | str = 1) -> "JsonRpcResponse":
        return cls(error={"code": code, "message": message}, id=rpc_id)
