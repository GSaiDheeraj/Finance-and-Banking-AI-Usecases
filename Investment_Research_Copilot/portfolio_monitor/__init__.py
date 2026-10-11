"""
Investment Research Copilot — Portfolio & Risk Monitoring (+ Construction).

A monitoring/advisory agent that runs over a LIVE, STRUCTURED portfolio (a positions
snapshot plus a cash-flow / transactions ledger), enriches it with free live market data,
and checks it against an investment policy (target allocation, tolerance bands, risk
limits). It tracks allocation, detects allocation drift and proposes rebalancing, reconciles
expected-vs-actual cash flows, computes portfolio risk metrics, scans for emerging market
risks, and raises prioritised alerts. It can also CONSTRUCT a portfolio from scratch for a
given mandate, in both a deterministic and an LLM-augmented mode that can be compared.

Design rule (shared with the other agents in this book): the LLM does *language* work only
— classify emerging-risk news, write the monitoring briefing, propose a construction with
reasons, answer questions. Every *decision-bearing* number — allocation, drift, rebalancing
trades, cash-flow variances, risk metrics, limit breaches, alert severities, the portfolio
risk score, and every hard constraint — is computed by deterministic Python, so the same
inputs always yield the same alerts and the same guardrailed portfolio.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
