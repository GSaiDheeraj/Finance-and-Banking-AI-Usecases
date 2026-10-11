"""Free, per-country research agents (geopolitical, sectoral, bond risk, bank FD/deposit
rates, and market risk) built on a real LangGraph `StateGraph` — fanned out per country via
LangGraph's `Send` API. Same free-sources-only, LLM-classifies/deterministic-decides pattern
as `osint/` (see that package's own docstring): the LLM only reads public snippets and
classifies them; the deterministic layer here decides how, if at all, a country's research
moves construction's candidate selection or monitoring's alerts/score.
"""
