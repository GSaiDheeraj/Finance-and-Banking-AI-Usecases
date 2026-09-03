"""
Free web-search wrappers — no API keys, no paid services.

The borrower's public footprint is gathered from DuckDuckGo (web + news) via the free
`ddgs` library, with `requests` + `beautifulsoup4` available to read a public page's
visible text. No login is performed and nothing behind a sign-in wall is fetched.

Each function degrades gracefully: if a library is missing or the network is
unavailable, it returns an empty result instead of crashing, so the rest of the credit
pipeline keeps working (the web-sentiment factors simply stay 'low').
"""
from __future__ import annotations

import os
import re
from typing import Dict, List

from ..logconf import get_logger

_log = get_logger()


# --- optional free dependencies (imported defensively) ---------------------
_IMPORT_NOTES: List[str] = []
try:                                            # web search + news
    from ddgs import DDGS                        # newer package name
except Exception:                               # pragma: no cover
    try:
        from duckduckgo_search import DDGS        # older package name
    except Exception:
        DDGS = None
        _IMPORT_NOTES.append("ddgs NOT installed (DuckDuckGo backend off)")

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:                               # pragma: no cover
    requests = None
    BeautifulSoup = None
    _IMPORT_NOTES.append("requests/beautifulsoup4 NOT installed (cannot read page text)")

for _note in _IMPORT_NOTES:
    _log.warning("osint import: %s", _note)
_log.info("osint backends ready: ddg=%s, page_fetch=%s", DDGS is not None, requests is not None)


def _timeout() -> float:
    try:
        return float(os.getenv("OSINT_TIMEOUT", "10"))
    except (TypeError, ValueError):
        return 10.0


def _region() -> str:
    return os.getenv("OSINT_REGION", "wt-wt")   # DuckDuckGo region ('wt-wt' = worldwide)


def web_search(query: str, max_results: int = 6, region: str = None) -> List[Dict]:
    """Free DuckDuckGo web search. Returns [{title, url, snippet, source}]."""
    if DDGS is None or not query.strip():
        return []
    region = region or _region()
    _log.debug("ddg.web: query=%r region=%r max=%d", query, region, max_results)
    out: List[Dict] = []
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
        _log.warning("ddg.web: %s: %s (returning %d result(s) so far)",
                      type(e).__name__, e, len(out))
        return out
    _log.debug("ddg.web: -> %d result(s)", len(out))
    return out


def news_search(query: str, max_results: int = 6, region: str = None) -> List[Dict]:
    """Free DuckDuckGo news search. Returns [{title, url, snippet, source, published}]."""
    if DDGS is None or not query.strip():
        return []
    region = region or _region()
    _log.debug("ddg.news: query=%r region=%r max=%d", query, region, max_results)
    out: List[Dict] = []
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
        _log.warning("ddg.news: %s: %s (returning %d result(s) so far)",
                      type(e).__name__, e, len(out))
        return out
    _log.debug("ddg.news: -> %d result(s)", len(out))
    return out


def fetch_page_text(url: str, max_chars: int = 4000) -> str:
    """Download a PUBLIC web page and return its visible text (no login, best-effort)."""
    if requests is None or BeautifulSoup is None or not url:
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CreditRiskBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=_timeout())
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        return text[:max_chars]
    except Exception:
        return ""


def search_available() -> bool:
    """True if the DuckDuckGo backend is importable."""
    return DDGS is not None
