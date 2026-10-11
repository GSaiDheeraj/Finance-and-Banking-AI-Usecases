"""Monitoring briefing — deterministic (templated) and LLM-augmented (grounded) variants.

Both consume the SAME deterministic analytics (allocation, drift, cash-flow variance, risk
metrics, alerts, score). The deterministic briefing assembles the narrative from those facts
with fixed templates (no LLM, fully offline). The LLM briefing writes readable prose grounded
strictly in the same facts — it never recomputes a number or changes an alert. Streamlit can
show both side-by-side for comparison.
"""
