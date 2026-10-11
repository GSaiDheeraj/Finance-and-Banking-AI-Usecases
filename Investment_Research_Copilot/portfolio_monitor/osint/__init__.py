"""Free emerging-market-risk scan (DuckDuckGo web/news; no API keys).

Collects recent public news around the portfolio's largest holdings, its sectors and the
macro backdrop, and turns it into a grounded `MarketRiskScan`. The LLM only *classifies* the
collected snippets into risk findings + an overall tone; the deterministic alert/score layer
decides how much (if at all) those findings move the portfolio's risk picture, applying a
credibility discount so public news never outranks a hard limit breach.
"""
