"""
Name-driven OSINT (open-source intelligence) profiling.

Given a person's name and a few details, gather their PUBLIC footprint from free web
sources (search, news, Wikipedia, public pages), then synthesize a 360° profile that
feeds the same deterministic risk-scoring/EDD engine used by the PDF onboarding flow.

Only publicly searchable information is collected — no logins, no private accounts, no
scraping of content behind authentication. This keeps the tool lawful for due diligence
and deliverable with free libraries.
"""
