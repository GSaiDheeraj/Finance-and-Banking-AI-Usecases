"""
Free web-search wrappers — no API keys, no paid services.

Everything here uses free, open libraries:
  * `ddgs` (DuckDuckGo search) — PRIMARY web search + news results,
  * `googlesearch-python` (free Google results scrape) — best-effort top-up only,
  * `wikipedia` for biographies,
  * `requests` + `beautifulsoup4` to read a public web page's text.

Web search uses DuckDuckGo first (it is the reliable free backend) and only tops up with
Google if more results are still needed. In practice Google's free scrape is throttled or
blocked in most environments and returns nothing, so DuckDuckGo carries the search.
Neither needs an API key.

Each function degrades gracefully: if a library is missing or the network is
unavailable, it returns an empty result instead of crashing, so the rest of the
pipeline keeps working. Nothing here requires a login, and no content behind a
sign-in wall is fetched.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, List


def _debug_on() -> bool:
    """Step-by-step tracing is ON by default; set OSINT_DEBUG=0 to silence it."""
    return (os.getenv("OSINT_DEBUG", "1") or "1").strip().lower() not in ("0", "false", "no", "")


def dbg(msg: str) -> None:
    """Print a step-by-step trace line to stderr (so it never pollutes captured output)."""
    if _debug_on():
        print(f"[osint] {msg}", file=sys.stderr, flush=True)

# --- optional free dependencies (imported defensively) ---------------------
_IMPORT_NOTES: List[str] = []
try:                                            # primary: free Google results scrape
    from googlesearch import search as _google_search
except Exception:                               # pragma: no cover
    _google_search = None
    _IMPORT_NOTES.append("googlesearch-python NOT installed (Google backend off)")

try:                                            # fallback web search + news
    from ddgs import DDGS                       # newer package name
except Exception:                               # pragma: no cover
    try:
        from duckduckgo_search import DDGS      # older package name
    except Exception:
        DDGS = None
        _IMPORT_NOTES.append("ddgs NOT installed (DuckDuckGo backend off)")

try:
    import wikipedia                            # biographies
except Exception:                               # pragma: no cover
    wikipedia = None
    _IMPORT_NOTES.append("wikipedia NOT installed (no encyclopedia lookups)")

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:                               # pragma: no cover
    requests = None
    BeautifulSoup = None
    _IMPORT_NOTES.append("requests/beautifulsoup4 NOT installed (cannot read page text)")

for _note in _IMPORT_NOTES:
    dbg(f"import: {_note}")
dbg("backends ready: "
    f"google={_google_search is not None}, ddg={DDGS is not None}, "
    f"wikipedia={wikipedia is not None}, page_fetch={requests is not None}")


def _timeout() -> float:
    try:
        return float(os.getenv("OSINT_TIMEOUT", "10"))
    except (TypeError, ValueError):
        return 10.0


def _region() -> str:
    return os.getenv("OSINT_REGION", "wt-wt")   # DuckDuckGo region ('wt-wt' = worldwide)


def _country() -> str:
    """2-letter country code for Google region (e.g. 'in', 'us'); '' = no preference."""
    return (os.getenv("OSINT_COUNTRY", "") or "").strip().lower()


def google_search(query: str, max_results: int = 6, region: str = None) -> List[Dict]:
    """Free Google results scrape (googlesearch-python). Region-aware, no API key.

    Mirrors: `search("name", num_results=100, region="in")`. Returns
    [{title, url, snippet, source}]. Empty if the library is missing or Google throttles.
    """
    if _google_search is None:
        dbg(f"google: SKIP (googlesearch-python not installed) — query={query!r}")
        return []
    if not query.strip():
        return []
    region = (region or _country() or None)
    dbg(f"google: query={query!r} region={region!r} max={max_results}")
    out: List[Dict] = []
    try:
        try:
            gen = _google_search(
                query, num_results=max_results, advanced=True,
                region=region, lang="en", sleep_interval=1.0, timeout=_timeout(),
            )
        except TypeError:
            # older googlesearch-python without region/timeout kwargs
            dbg("google: region/timeout kwargs unsupported, retrying minimal call")
            gen = _google_search(query, num_results=max_results, advanced=True)
        for r in gen:
            # advanced=True yields objects with .url/.title/.description; plain mode yields str
            if isinstance(r, str):
                out.append({"title": None, "url": r, "snippet": None, "source": "web"})
            else:
                out.append({
                    "title": getattr(r, "title", None),
                    "url": getattr(r, "url", None),
                    "snippet": getattr(r, "description", None),
                    "source": "web",
                })
    except Exception as e:
        dbg(f"google: ERROR {type(e).__name__}: {e} (returning {len(out)} so far)")
        return out
    dbg(f"google: -> {len(out)} result(s)")
    for r in out:
        dbg(f"   google · {r.get('url')}  | {(r.get('title') or '')[:70]}")
    return out


def _ddg_text(query: str, max_results: int = 6) -> List[Dict]:
    """Free DuckDuckGo web search. Returns [{title, url, snippet, source}]."""
    if DDGS is None:
        dbg(f"ddg: SKIP (ddgs not installed) — query={query!r}")
        return []
    if not query.strip():
        return []
    dbg(f"ddg: query={query!r} region={_region()!r} max={max_results}")
    out: List[Dict] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=_region(), max_results=max_results) or []:
                out.append({
                    "title": r.get("title"),
                    "url": r.get("href") or r.get("url"),
                    "snippet": r.get("body") or r.get("snippet"),
                    "source": "web",
                })
    except Exception as e:
        dbg(f"ddg: ERROR {type(e).__name__}: {e} (returning {len(out)} so far)")
        return out
    dbg(f"ddg: -> {len(out)} result(s)")
    return out


def web_search(query: str, max_results: int = 6, region: str = None) -> List[Dict]:
    """Aggregate free web search: DuckDuckGo first (primary), topped up with Google.

    DuckDuckGo is the reliable free backend and drives the results. Google's free scrape
    is throttled/blocked in most environments (returns nothing), so it is only used to
    top up if DuckDuckGo did not fill `max_results`. De-duplicated by link/snippet.
    """
    if not query.strip():
        return []
    results: List[Dict] = []
    seen = set()

    def _merge(rows: List[Dict]) -> None:
        for r in rows:
            url = r.get("url")
            key = url or (r.get("snippet") or r.get("title") or "")[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(r)

    # 1) DuckDuckGo — primary, reliable, no API key.
    _merge(_ddg_text(query, max_results))
    # 2) Google — best-effort top-up only if we still need more (usually yields nothing).
    if len(results) < max_results:
        _merge(google_search(query, max_results, region))

    dbg(f"web_search: query={query!r} -> {len(results[:max_results])} merged result(s) "
        f"(ddg-primary)")
    return results[:max_results]


def news_search(query: str, max_results: int = 6) -> List[Dict]:
    """Free DuckDuckGo news search. Returns [{title, url, snippet, source, published}]."""
    if DDGS is None or not query.strip():
        return []
    dbg(f"news: query={query!r} region={_region()!r} max={max_results}")
    out: List[Dict] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, region=_region(), max_results=max_results) or []:
                out.append({
                    "title": r.get("title"),
                    "url": r.get("url") or r.get("href"),
                    "snippet": r.get("body") or r.get("excerpt"),
                    "source": "news",
                    "published": r.get("date"),
                    "publisher": r.get("source"),
                })
        print(out)
    except Exception as e:
        dbg(f"news: ERROR {type(e).__name__}: {e} (returning {len(out)} so far)")
        return out
    dbg(f"news: -> {len(out)} result(s)")
    return out


def wikipedia_summary(name: str, sentences: int = 6) -> List[Dict]:
    """Free Wikipedia lookup. Returns up to one [{title, url, snippet, source}]."""
    if wikipedia is None:
        dbg(f"wikipedia: SKIP (wikipedia not installed) — name={name!r}")
        return []
    if not name.strip():
        return []
    dbg(f"wikipedia: search {name!r}")
    try:
        titles = wikipedia.search(name, results=3)
        dbg(f"wikipedia: candidate titles -> {titles}")
        if not titles:
            return []
        try:
            page = wikipedia.page(titles[0], auto_suggest=False)
        except Exception:
            # disambiguation or redirect — fall back to a plain summary
            summary = wikipedia.summary(titles[0], sentences=sentences, auto_suggest=False)
            return [{"title": titles[0], "url": None, "snippet": summary, "source": "wikipedia"}]
        return [{
            "title": page.title,
            "url": getattr(page, "url", None),
            "snippet": (page.summary or "")[: sentences * 240],
            "source": "wikipedia",
        }]
    except Exception as e:
        dbg(f"wikipedia: ERROR {type(e).__name__}: {e}")
        return []


def fetch_page_text(url: str, max_chars: int = 4000) -> str:
    """Download a PUBLIC web page and return its visible text (no login, best-effort)."""
    if requests is None or BeautifulSoup is None or not url:
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DueDiligenceBot/1.0)"}
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
    """True if at least one web-search backend (Google or DuckDuckGo) is importable."""
    return _google_search is not None or DDGS is not None
