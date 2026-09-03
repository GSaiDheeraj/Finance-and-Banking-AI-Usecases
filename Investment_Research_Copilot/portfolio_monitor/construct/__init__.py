"""Portfolio construction from scratch — deterministic optimizer and LLM-augmented advisor.

Both constructors emit the same `ConstructedPortfolio` schema (so they can be compared) and
both pass their proposed weights through the shared deterministic `guardrails` so no hard
constraint (single-name cap, sector cap, exclusions, min position) can ever be breached —
including by the LLM.
"""
