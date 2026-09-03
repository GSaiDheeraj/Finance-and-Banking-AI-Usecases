"""
Free web-search wrappers — no API keys, no paid services.

Recent public news around the portfolio is gathered from DuckDuckGo (web + news) via the
free `ddgs` library. No login is performed and nothing behind a sign-in wall is fetched.

**Always pass company names (not tickers) to these wrappers.**  yfinance works with tickers;
DuckDuckGo works with company names.  `RELIANCE.NS stock risk` returns nothing; "Reliance
Industries risk" returns results.

Error handling:
  * `DDGSException: No results found` — the query was too specific; we retry once with a
    simpler fallback (the company name only). This is the most common cause of zero results
    with well-formed queries and a healthy network.
  * `DDGSException: Ratelimit` — DuckDuckGo temporarily throttled us; we back off and return
    whatever we have. Reduce `PM_NEWS_MAX_RESULTS` or add `PM_NEWS_DELAY` to slow down calls.
  * Any other network / parsing error — logged and returns empty gracefully.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional


def _debug_on() -> bool:
    return (os.getenv("PM_NEWS_DEBUG", "1") or "1").strip().lower() not in ("0", "false", "no", "")


def dbg(msg: str) -> None:
    if _debug_on():
        print(f"[news] {msg}", file=sys.stderr, flush=True)


_IMPORT_NOTES: List[str] = []
try:
    from ddgs import DDGS                            # newer package name
except Exception:                                   # pragma: no cover
    try:
        from duckduckgo_search import DDGS           # older package name
    except Exception:
        DDGS = None
        _IMPORT_NOTES.append("ddgs NOT installed (DuckDuckGo backend off)")

for _note in _IMPORT_NOTES:
    dbg(f"import: {_note}")


def _region() -> str:
    return os.getenv("PM_NEWS_REGION", "wt-wt")


def _delay() -> float:
    """Per-request delay in seconds. Increase PM_NEWS_DELAY to avoid rate-limit errors."""
    try:
        return float(os.getenv("PM_NEWS_DELAY", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _is_no_results(exc: Exception) -> bool:
    return "no results" in str(exc).lower()


def _is_ratelimit(exc: Exception) -> bool:
    return "ratelimit" in str(exc).lower()


def search_available() -> bool:
    """True if the DuckDuckGo backend is importable."""
    return DDGS is not None


def news_search(
    query: str,
    max_results: int = 6,
    region: Optional[str] = None,
    fallback_query: Optional[str] = None,
) -> List[Dict]:
    """Free DuckDuckGo news search. Returns [{title, url, snippet, source, published}].

    Pass company names — not tickers — for the query. If the search returns no results,
    retries once with `fallback_query` when supplied (typically just the company name).
    """
    if DDGS is None or not query.strip():
        return []
    region = region or _region()
    dbg(f"ddg.news: query={query!r} region={region!r} max={max_results}")
    out: List[Dict] = []
    time.sleep(_delay())
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, region=region, max_results=max_results) or []:
                out.append({
                    "title": r.get("title"),
                    "url": r.get("url") or r.get("href"),
                    "snippet": r.get("body") or r.get("excerpt"),
                    "source": "news",
                    "published": r.get("date"),
                    "publisher": r.get("source"),
                })
    except Exception as e:
        if _is_no_results(e):
            # "No results found" means DuckDuckGo understood the query but found nothing —
            # typical for exchange-suffixed tickers or very niche queries.  Retry with a
            # shorter, simpler fallback (usually just the company name).
            dbg(f"ddg.news: no results for {query!r} — "
                + ("retrying with fallback" if fallback_query else "no fallback, skipping"))
            if fallback_query and fallback_query.strip() != query.strip():
                return news_search(fallback_query, max_results, region, fallback_query=None)
        elif _is_ratelimit(e):
            dbg(f"ddg.news: rate-limited — sleeping 5s then skipping query {query!r}")
            time.sleep(5)
        else:
            dbg(f"ddg.news: ERROR {type(e).__name__}: {e} (returning {len(out)} so far)")
        return out
    dbg(f"ddg.news: -> {len(out)} result(s)")
    return out


def web_search(
    query: str,
    max_results: int = 6,
    region: Optional[str] = None,
    fallback_query: Optional[str] = None,
) -> List[Dict]:
    """Free DuckDuckGo web search. Returns [{title, url, snippet, source}].

    Same retry logic as news_search: retries with fallback_query on "no results".
    """
    if DDGS is None or not query.strip():
        return []
    region = region or _region()
    dbg(f"ddg.web: query={query!r} region={region!r} max={max_results}")
    out: List[Dict] = []
    time.sleep(_delay())
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=region, max_results=max_results) or []:
                out.append({
                    "title": r.get("title"),
                    "url": r.get("href") or r.get("url"),
                    "snippet": r.get("body") or r.get("snippet"),
                    "source": "web",
                })
    except Exception as e:
        if _is_no_results(e):
            dbg(f"ddg.web: no results for {query!r} — "
                + ("retrying with fallback" if fallback_query else "no fallback, skipping"))
            if fallback_query and fallback_query.strip() != query.strip():
                return web_search(fallback_query, max_results, region, fallback_query=None)
        elif _is_ratelimit(e):
            dbg(f"ddg.web: rate-limited — sleeping 5s then skipping query {query!r}")
            time.sleep(5)
        else:
            dbg(f"ddg.web: ERROR {type(e).__name__}: {e} (returning {len(out)} so far)")
        return out
    dbg(f"ddg.web: -> {len(out)} result(s)")
    return out
