"""Portfolio construction from scratch — deterministic optimizer and LLM+VLRT advisor.

Both constructors emit the same `ConstructedPortfolio` schema (so they can be compared) and
both pass their proposed weights through the shared deterministic `guardrails` so no hard
constraint (single-name cap, sector cap, exclusions, min position) can ever be breached —
including by the LLM.

The LLM path runs committee(s) first — Quant-style VLRT (`vlrt_agents`: Valuation,
Liquidity, Risk Appetite, Timing), ICICI-style VCST (`vcst_agents`:
Valuation, Cycle, Sentiment from live stock/sector search, Trigger),
Motilal Oswal-style SQL (`sql_agents`: Size, Quality, Longevity) and/or
WhiteOak-style OPCO-FINCO (`opcofinco_agents`: OpCo, FinCo, Business with live
governance/disruption search, Value) and/or Kotak-style BMV (`bmv_agents`:
Business, Management with live governance search, Valuation as a sequential
gate) per `ConstructionRequest.framework`
('vlrt' | 'vcst' | 'sql' | 'opcofinco' | 'bmv' | 'both' | 'all') —
deterministic scores always, LLM commentary when available — and the
portfolio-manager LLM must build USING those scores with a reason per pick
per active framework.
"""
