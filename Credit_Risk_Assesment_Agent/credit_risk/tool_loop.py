"""
A small, bounded LangChain tool-calling loop.

Each grounded extractor needs the LLM to retrieve its own evidence (via `search_pages`/
`get_page`) rather than have Python pre-fetch a fixed top-k by one hardcoded query
string. The exchange is short — a handful of searches then a final JSON answer — so a
hand-rolled bounded loop is simpler than pulling in LangGraph's prebuilt ReAct agent for
something with no branching or persistence need: bind tools, invoke, execute any tool
calls, repeat, until the model answers with no more tool calls or a fixed iteration cap
is hit.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

DEFAULT_MAX_ITERATIONS = 6

_STOP_NOW_MESSAGE = HumanMessage(content=(
    "You've searched enough. Answer now with ONLY the JSON object/array requested — "
    "no further tool calls, no prose."
))


def run_tool_calling_loop(
    llm: Any,
    tools: list[BaseTool],
    messages: list[BaseMessage],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Let `llm` call `tools` against a growing `messages` transcript, bounded by `max_iterations`.

    Returns the model's final text content. If the model is still requesting tool calls
    when the cap is hit, one last turn tells it to stop and answer instead of looping
    forever — the failure path a runaway/looping model would otherwise hit.

    That forced-final turn still goes through `bound_llm`, not the plain `llm`: some
    gateways (this project's included — an AWS Bedrock Converse-API backend) reject any
    call whose message history already contains tool_use/tool_result blocks unless tools
    are declared on that call too, even when the prompt tells the model not to use them.
    Binding tools doesn't force a tool call, so this is still just a strong hint the
    model is free to ignore — the loop returns whatever it says either way.
    """
    tool_map = {tool.name: tool for tool in tools}
    bound_llm = llm.bind_tools(tools)
    transcript = list(messages)

    for _ in range(max_iterations):
        response = bound_llm.invoke(transcript)
        transcript.append(response)
        if not getattr(response, "tool_calls", None):
            return response.content
        for call in response.tool_calls:
            transcript.append(_invoke_tool(tool_map, call))

    final = bound_llm.invoke(transcript + [_STOP_NOW_MESSAGE])
    return final.content


def _invoke_tool(tool_map: dict[str, BaseTool], call: dict[str, Any]) -> ToolMessage:
    tool = tool_map.get(call["name"])
    if tool is None:
        content: Any = f"Unknown tool: {call['name']}"
    else:
        try:
            content = tool.invoke(call["args"])
        except Exception as exc:
            # A bad tool call (e.g. a page number outside the pack) shouldn't crash
            # extraction — feed the error back so the model can retry differently.
            content = f"Tool error: {type(exc).__name__}: {exc}"
    return ToolMessage(content=json.dumps(content, default=str), tool_call_id=call["id"])
