"""Fetch local news from three free sources and normalise them to plain dicts.

  1. Google News RSS search      free, no key
  2. Your own RSS feeds          free, set NEWS_RSS_FEEDS="url1,url2" (local paper / TV station feeds)
  3. NewsAPI.org                 free developer key, set NEWSAPI_KEY (https://newsapi.org/register)

Each article: {"url", "title", "summary", "source", "published"}.
"""
import html
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests

HEADERS = {"User-Agent": "NeighborhoodWatchApp/1.0"}
TOPICS = ('crash OR collision OR fire OR "road closed" OR closure OR police OR shooting '
          'OR robbery OR outage OR missing OR evacuation OR flooding OR "gas leak"')


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(xml_text, default_source):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        title = _clean(it.findtext("title"))
        if not link or not title:
            continue
        src = it.find("source")
        source = src.text if src is not None and src.text else default_source
        if title.endswith(f" - {source}"):            # Google News appends " - Publisher"
            title = title[: -len(source) - 3]
        summary = _clean(it.findtext("description"))
        if summary.startswith(title):                 # Google News repeats the headline as the summary
            summary = ""
        items.append({"url": link, "title": title, "summary": summary[:600], "source": source,
                      "published": it.findtext("pubDate") or ""})
    return items


def _get(url, **kw):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"[news] fetch failed {url[:80]}: {e}", flush=True)
        return None


def google_news(city, state):
    q = f'"{city}" {state} ({TOPICS}) when:2d'
    r = _get(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en")
    return _parse_rss(r.text, "Google News") if r else []


def custom_feeds(city):
    """Articles from NEWS_RSS_FEEDS that mention the city (local outlets cover a wider area)."""
    out = []
    for url in [u.strip() for u in os.environ.get("NEWS_RSS_FEEDS", "").split(",") if u.strip()]:
        r = _get(url)
        if not r:
            continue
        for a in _parse_rss(r.text, url.split("/")[2]):
            if not city or city.lower() in f"{a['title']} {a['summary']}".lower():
                out.append(a)
    return out


def newsapi(city, state):
    key = os.environ.get("NEWSAPI_KEY")
    if not key:
        return []
    r = _get("https://newsapi.org/v2/everything",
             params={"q": f'"{city}" AND ({TOPICS})', "language": "en", "sortBy": "publishedAt",
                     "pageSize": 20},
             headers={**HEADERS, "X-Api-Key": key})
    if not r:
        return []
    return [{"url": a["url"], "title": _clean(a.get("title")), "summary": _clean(a.get("description"))[:600],
             "source": (a.get("source") or {}).get("name") or "NewsAPI", "published": a.get("publishedAt") or ""}
            for a in r.json().get("articles", []) if a.get("url") and a.get("title")]


def fetch_for_area(city, state):
    seen, out = set(), []
    for a in google_news(city, state) + custom_feeds(city) + newsapi(city, state):
        if a["url"] not in seen:
            seen.add(a["url"])
            out.append(a)
    return out


def sources_status():
    return {"google": True,
            "rss": bool(os.environ.get("NEWS_RSS_FEEDS", "").strip()),
            "newsapi": bool(os.environ.get("NEWSAPI_KEY"))}
