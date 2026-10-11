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

from ..logconf import get_logger

_log = get_logger()


# --- optional free dependencies (imported defensively) ---------------------
_IMPORT_NOTES: list[str] = []
try:  # web search + news
    from ddgs import DDGS  # newer package name
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # older package name
    except ImportError:
        DDGS = None
        _IMPORT_NOTES.append("ddgs NOT installed (DuckDuckGo backend off)")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None
    _IMPORT_NOTES.append(
        "requests/beautifulsoup4 NOT installed (cannot read page text)"
    )

for _import_note in _IMPORT_NOTES:
    _log.warning("osint import: %s", _import_note)
_log.info(
    "osint backends ready: ddg=%s, page_fetch=%s",
    DDGS is not None,
    requests is not None,
)

DEFAULT_OSINT_TIMEOUT_SECONDS = 10.0
DEFAULT_OSINT_REGION = "wt-wt"  # DuckDuckGo region ('wt-wt' = worldwide)
DEFAULT_SEARCH_RESULTS = 6
DEFAULT_PAGE_CHARS = 4000
NON_HTML_STATUS = 200


def _timeout() -> float:
    """Return the OSINT HTTP timeout, falling back to default on malformed env."""
    try:
        return float(os.getenv("OSINT_TIMEOUT", str(DEFAULT_OSINT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_OSINT_TIMEOUT_SECONDS


def _region() -> str:
    """Return the DuckDuckGo search region."""
    return os.getenv("OSINT_REGION", DEFAULT_OSINT_REGION)


def _run_ddg_search(
    query: str, max_results: int, region: str | None, search_kind: str
) -> list[dict]:
    """Run a DuckDuckGo search of the given kind, returning partial results on failure."""
    if DDGS is None or not query.strip():
        return []
    active_region = region or _region()
    _log.debug(
        "ddg.%s: query=%r region=%r max=%d",
        search_kind,
        query,
        active_region,
        max_results,
    )
    collected: list[dict] = []
    try:
        with DDGS() as ddg_client:
            if search_kind == "news":
                raw_results = ddg_client.news(
                    query, region=active_region, max_results=max_results
                )
            else:
                raw_results = ddg_client.text(
                    query, region=active_region, max_results=max_results
                )
            for entry in raw_results or []:
                collected.append(_normalise_result(entry, search_kind))
    # Free search fails heterogeneously; keep partial results.
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "ddg.%s: %s: %s (returning %d result(s) so far)",
            search_kind,
            type(exc).__name__,
            exc,
            len(collected),
        )
        return collected
    _log.debug("ddg.%s: -> %d result(s)", search_kind, len(collected))
    return collected


def _normalise_result(entry: dict, search_kind: str) -> dict:
    """Map a raw DuckDuckGo entry to the common {title, url, snippet, source} shape."""
    if search_kind == "news":
        return {
            "title": entry.get("title"),
            "url": entry.get("url") or entry.get("href"),
            "snippet": entry.get("body") or entry.get("excerpt"),
            "source": "news",
            "published": entry.get("date"),
            "publisher": entry.get("source"),
        }
    return {
        "title": entry.get("title"),
        "url": entry.get("href") or entry.get("url"),
        "snippet": entry.get("body") or entry.get("snippet"),
        "source": "web",
    }


def web_search(
    query: str, max_results: int = DEFAULT_SEARCH_RESULTS, region: str | None = None
) -> list[dict]:
    """Free DuckDuckGo web search. Returns [{title, url, snippet, source}]."""
    return _run_ddg_search(query, max_results, region, "web")


def news_search(
    query: str, max_results: int = DEFAULT_SEARCH_RESULTS, region: str | None = None
) -> list[dict]:
    """Free DuckDuckGo news search. Returns [{title, url, snippet, source, published}]."""
    return _run_ddg_search(query, max_results, region, "news")


def fetch_page_text(url: str, max_chars: int = DEFAULT_PAGE_CHARS) -> str:
    """Download a PUBLIC web page and return its visible text (no login, best-effort)."""
    if requests is None or BeautifulSoup is None or not url:
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CreditRiskBot/1.0)"}
    try:
        response = requests.get(url, headers=headers, timeout=_timeout())
        if response.status_code != NON_HTML_STATUS:
            return ""
        if "text/html" not in response.headers.get("content-type", ""):
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        return text[:max_chars]
    # Any fetch/parse failure degrades to empty text.
    except Exception as exc:  # noqa: BLE001
        _log.debug(
            "fetch_page_text failed for %r: %s: %s", url, type(exc).__name__, exc
        )
        return ""


def search_available() -> bool:
    """True if the DuckDuckGo backend is importable."""
    return DDGS is not None
