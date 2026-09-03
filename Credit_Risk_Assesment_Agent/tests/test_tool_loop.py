"""Unit tests for credit_risk.tool_loop.run_tool_calling_loop."""
from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from credit_risk.tool_loop import run_tool_calling_loop


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo:{text}"


def _make_llm(responses):
    """A fake chat model: bind_tools returns itself, invoke() yields `responses` in order."""
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.invoke.side_effect = responses
    return llm


def test_returns_final_answer_when_model_stops_calling_tools():
    final = AIMessage(content='{"ok": true}')
    llm = _make_llm([
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "1"}]),
        final,
    ])

    result = run_tool_calling_loop(llm, [echo], [HumanMessage(content="go")])

    assert result == '{"ok": true}'
    assert llm.invoke.call_count == 2


def test_never_calls_tools_returns_immediately():
    llm = _make_llm([AIMessage(content='{"ok": true}')])

    result = run_tool_calling_loop(llm, [echo], [HumanMessage(content="go")])

    assert result == '{"ok": true}'
    assert llm.invoke.call_count == 1


def test_stops_at_max_iterations_instead_of_looping_forever():
    """A model that always requests a tool call must not spin forever — the loop caps
    at `max_iterations` bound calls, then forces one final no-tools answer."""
    always_calling = AIMessage(
        content="", tool_calls=[{"name": "echo", "args": {"text": "again"}, "id": "1"}]
    )
    forced_final = AIMessage(content='{"forced": true}')
    # bind_tools().invoke() is called `max_iterations` times (always requesting a tool),
    # then the plain (unbound) llm.invoke() is called once more for the forced answer.
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.invoke.side_effect = [always_calling] * 3 + [forced_final]

    result = run_tool_calling_loop(llm, [echo], [HumanMessage(content="go")], max_iterations=3)

    assert result == '{"forced": true}'
    assert llm.invoke.call_count == 4  # 3 bound-loop turns + 1 forced final turn


def test_tool_error_is_fed_back_instead_of_raised():
    """A tool that raises must not crash the loop — the error becomes a ToolMessage the
    model can see and react to."""
    @tool
    def failing_tool(x: str) -> str:
        """Always raises."""
        raise ValueError("boom")

    final = AIMessage(content='{"recovered": true}')
    llm = _make_llm([
        AIMessage(content="", tool_calls=[{"name": "failing_tool", "args": {"x": "y"}, "id": "1"}]),
        final,
    ])

    result = run_tool_calling_loop(llm, [failing_tool], [HumanMessage(content="go")])

    assert result == '{"recovered": true}'
