"""
News Sentiment Analyzer
─────────────────────────────────────────────
Fetches RSS headlines from Indian financial news sources.
Scores each headline for market sentiment using keyword matching.
Returns an overall sentiment score and market-moving news alerts.

Score: -10 (very bearish) to +10 (very bullish)

Install dependencies:  pip install feedparser requests
"""

import re
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

logger = logging.getLogger("NewsSentiment")

# ─────────────────────────────────────────────
# SENTIMENT KEYWORD WEIGHTS
# ─────────────────────────────────────────────

BULLISH_KEYWORDS = {
    "rate cut": 3, "stimulus": 3, "rally": 3, "record high": 3, "bull run": 3,
    "gdp growth": 3, "strong earnings": 3, "rate pause": 2, "fed pivot": 3,
    "buyback": 2, "dividend": 2, "upgrade": 2, "outperform": 2,
    "recovery": 2, "growth": 2, "surge": 2, "gain": 2, "positive": 2,
    "optimism": 2, "upturn": 2, "expansion": 2, "profit rise": 2,
    "fii buying": 3, "dii buying": 2, "inflow": 2, "fdi": 2,
    "up": 1, "rise": 1, "rises": 1, "rose": 1, "advance": 1, "advances": 1,
    "green": 1, "support": 1, "gains": 2, "gained": 2,
    "stable": 1, "steady": 1, "beat estimate": 2, "above estimate": 2,
    "higher": 1, "bullish": 2, "buy": 1, "strong": 2,
    # Commodity bullish (falling crude/oil is good for India)
    "crude falls": 2, "oil falls": 2, "crude down": 2, "oil down": 2,
    "gold rises": 1, "gold up": 1, "silver up": 1,
    "rupee strengthens": 2, "rupee gains": 2, "rupee up": 1,
    "import duty cut": 2, "commodity softens": 1,
    "mcx gold gains": 2, "mcx silver gains": 1,
}

BEARISH_KEYWORDS = {
    "rate hike": -3, "recession": -3, "crash": -3, "selloff": -3,
    "market crash": -3, "war": -3, "conflict": -3, "default": -3,
    "crisis": -3, "collapse": -3, "fed hawkish": -3, "inflation surge": -3,
    "fii selling": -3, "outflow": -2, "downgrade": -2,
    "fall": -2, "falls": -2, "fell": -2, "decline": -2, "declined": -2,
    "drop": -2, "dropped": -2, "loss": -2, "weak": -2,
    "slowdown": -2, "miss estimate": -2, "below estimate": -2,
    "profit warning": -3, "downfall": -2, "bearish": -2,
    "down": -1, "red": -1, "pressure": -1, "volatility": -1,
    "uncertainty": -1, "caution": -1, "concern": -1,
    "lower": -1, "falls": -2, "sell": -1, "slump": -2,
    # Commodity bearish (crude/oil rising hurts India — import bill grows)
    "crude surges": -3, "oil surges": -3, "crude rises": -2, "oil rises": -2,
    "crude up": -2, "oil up": -2, "brent rises": -2, "wti rises": -2,
    "rupee weakens": -2, "rupee falls": -2, "rupee slips": -2,
    "import duty hike": -2, "inflation rises": -2,
    "mcx gold falls": -1, "gold slips": -1,
}

HIGH_IMPACT_EVENTS = [
    "rbi policy", "rbi rate", "monetary policy", "mpc meeting",
    "fed meeting", "fomc", "us cpi", "inflation data", "gdp data",
    "nifty circuit", "market halt", "trading halt", "election result",
    "budget", "union budget", "interim budget", "ipo listing",
    "f&o expiry", "futures expiry", "derivatives expiry",
    "rbi governor", "interest rate", "repo rate",
]

# ─────────────────────────────────────────────
# RSS SOURCES  (priority order — most reliable first)
# ─────────────────────────────────────────────

RSS_FEEDS = {
    # ── Indian financial news ──
    "Economic Times":    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "MoneyControl":      "https://www.moneycontrol.com/rss/marketreports.xml",
    "NDTV Profit":       "https://feeds.feedburner.com/ndtvprofit-latest",
    "Mint Markets":      "https://www.livemint.com/rss/markets",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    "Business Line":     "https://www.thehindubusinessline.com/markets/stock-markets/?service=rss",
    # ── TV channels (India) ──
    "CNBC TV18":         "https://www.cnbctv18.com/commonfeeds/v1/ind/rss/market.xml",
    # ── International ──
    "CNBC US":           "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
    "Wall St Journal":   "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "Bloomberg":         "https://feeds.bloomberg.com/markets/news.rss",
    # ── Google News (CNBC Awaaz + Dalal Street + commodities) ──
    "Google-CNBC Awaaz": "https://news.google.com/rss/search?q=CNBC+Awaaz+market+nifty&hl=en-IN&gl=IN&ceid=IN:en",
    "Google-Dalal St":   "https://news.google.com/rss/search?q=Dalal+Street+Journal+nifty+india+stock&hl=en-IN&gl=IN&ceid=IN:en",
    # ── Commodities (MCX, gold, crude, silver) ──
    "ET Commodities":    "https://economictimes.indiatimes.com/commodities/rssfeeds/1368159325.cms",
    "Google-MCX":        "https://news.google.com/rss/search?q=MCX+gold+silver+crude+oil+commodity+India&hl=en-IN&gl=IN&ceid=IN:en",
    "Google-Commodity2": "https://news.google.com/rss/search?q=gold+price+crude+oil+rupee+dollar+india&hl=en-IN&gl=IN&ceid=IN:en",
}

# Browser-like UA to avoid 403 blocks
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent":      _UA,
    "Accept":          "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
}

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class NewsItem:
    title:     str
    source:    str
    url:       str
    timestamp: datetime
    score:     float = 0.0
    is_high_impact: bool = False
    keywords_found: List[str] = field(default_factory=list)

@dataclass
class SentimentResult:
    overall_score:    float   = 0.0
    signal:           str     = "NEUTRAL"
    confidence:       float   = 0.0
    high_impact_news: List[NewsItem] = field(default_factory=list)
    recent_headlines: List[NewsItem] = field(default_factory=list)
    source_scores:    Dict[str, float] = field(default_factory=dict)
    last_updated:     Optional[datetime] = None
    error:            Optional[str] = None


# ─────────────────────────────────────────────
# SENTIMENT ANALYZER
# ─────────────────────────────────────────────

class NewsSentimentAnalyzer:
    """
    Fetches and scores news headlines for market sentiment.
    Caches results for 15 minutes to avoid repeated fetching.
    Uses feedparser (preferred) or built-in XML/regex fallback.
    """

    CACHE_MINUTES = 15
    MAX_AGE_HOURS = 6     # Accept news up to 6 hours old (covers pre-market; avoids yesterday's noise)

    def __init__(self):
        self._cache: Optional[SentimentResult] = None
        self._cache_time: Optional[datetime] = None
        try:
            import feedparser
            self._feedparser = feedparser
            logger.info("[News] feedparser available")
        except ImportError:
            self._feedparser = None
            logger.warning("[News] feedparser not installed — using built-in XML parser. "
                           "Run: pip install feedparser   for best results.")

    def get_sentiment(self, force_refresh: bool = False) -> SentimentResult:
        """Return cached sentiment or fetch fresh if cache expired."""
        if (not force_refresh and
                self._cache is not None and
                self._cache_time is not None and
                datetime.now() - self._cache_time < timedelta(minutes=self.CACHE_MINUTES)):
            return self._cache

        result = self._fetch_and_score()
        self._cache = result
        self._cache_time = datetime.now()
        return result

    def _fetch_and_score(self) -> SentimentResult:
        """Fetch all RSS feeds and compute aggregate sentiment."""
        all_items: List[NewsItem] = []
        source_scores: Dict[str, float] = {}
        errors: List[str] = []

        for source_name, feed_url in RSS_FEEDS.items():
            try:
                items = self._fetch_feed(feed_url, source_name)
                if items:
                    src_score = sum(i.score for i in items) / len(items)
                    source_scores[source_name] = round(src_score, 2)
                    all_items.extend(items)
                    logger.info(f"[News] {source_name}: {len(items)} items, score={src_score:+.2f}")
                else:
                    source_scores[source_name] = 0.0
                    logger.warning(f"[News] {source_name}: 0 items (feed may be empty or filtered)")
            except Exception as e:
                err_msg = f"{source_name}: {str(e)[:60]}"
                logger.warning(f"[News] Failed {source_name}: {e}")
                source_scores[source_name] = 0.0
                errors.append(err_msg)

        if not all_items:
            error_str = "No news fetched"
            if errors:
                error_str += " — " + "; ".join(e[:50] for e in errors[:3])
            # Extra hint if no internet
            error_str += ". Check internet / run: pip install feedparser"
            return SentimentResult(
                signal        = "NEUTRAL",
                error         = error_str,
                source_scores = source_scores,
                last_updated  = datetime.now(),
            )

        # Sort by score magnitude (most impactful first)
        all_items.sort(key=lambda x: abs(x.score), reverse=True)

        # Weighted average (top 20 items, decaying weight)
        total_score = 0.0
        total_weight = 0.0
        for i, item in enumerate(all_items[:20]):
            weight = 1.0 / (1 + i * 0.1)
            total_score  += item.score * weight
            total_weight += weight

        overall = total_score / total_weight if total_weight > 0 else 0.0
        overall = max(-10, min(10, overall))

        if overall >= 2.0:
            signal     = "BULLISH"
            confidence = min((overall / 10) * 100, 100)
        elif overall <= -2.0:
            signal     = "BEARISH"
            confidence = min((abs(overall) / 10) * 100, 100)
        else:
            signal     = "NEUTRAL"
            confidence = (1 - abs(overall) / 2) * 100

        high_impact = [i for i in all_items if i.is_high_impact]

        logger.info(
            f"[News] Score={overall:+.1f} Signal={signal} "
            f"Items={len(all_items)} HighImpact={len(high_impact)}"
        )

        return SentimentResult(
            overall_score    = round(overall, 2),
            signal           = signal,
            confidence       = round(confidence, 1),
            high_impact_news = high_impact[:5],
            recent_headlines = all_items[:10],
            source_scores    = source_scores,
            last_updated     = datetime.now(),
        )

    # ─────────────────────────────────────────────
    # UNIFIED FEED FETCHER
    # ─────────────────────────────────────────────

    def _fetch_feed(self, url: str, source: str) -> List[NewsItem]:
        """Try feedparser first, then XML fallback, then regex last-resort."""
        if self._feedparser is not None:
            return self._parse_with_feedparser(url, source)
        return self._parse_with_xml(url, source)

    # ─────────────────────────────────────────────
    # PARSER 1 — feedparser (best, handles RSS + Atom + namespaces)
    # ─────────────────────────────────────────────

    def _parse_with_feedparser(self, url: str, source: str) -> List[NewsItem]:
        """Pre-fetch with requests for headers control, then parse with feedparser."""
        content = self._http_get(url)
        feed = self._feedparser.parse(content)

        if feed.bozo and hasattr(feed, "bozo_exception"):
            logger.debug(f"[News] {source} bozo: {type(feed.bozo_exception).__name__}")

        if not feed.entries:
            raise RuntimeError(f"feedparser: 0 entries (bozo={feed.bozo})")

        items = []
        cutoff = datetime.now() - timedelta(hours=self.MAX_AGE_HOURS)

        for entry in feed.entries:
            title = (getattr(entry, "title", "") or "").strip()
            link  = (getattr(entry, "link",  "") or "").strip()
            if not title:
                continue

            ts = self._entry_timestamp(entry)
            if ts < cutoff:
                continue

            scored = self._score_title(title, source, link, ts)
            if scored:
                items.append(scored)

        # No items within cutoff — fall back to latest 5 regardless of age
        if not items and feed.entries:
            for entry in feed.entries[:5]:
                title = (getattr(entry, "title", "") or "").strip()
                link  = (getattr(entry, "link",  "") or "").strip()
                if title:
                    scored = self._score_title(title, source, link, datetime.now())
                    if scored:
                        items.append(scored)

        return items

    def _entry_timestamp(self, entry) -> datetime:
        """Extract best available timestamp from a feedparser entry."""
        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    return datetime(*val[:6])
                except Exception:
                    pass
        return datetime.now()

    # ─────────────────────────────────────────────
    # PARSER 2 — xml.etree fallback (no feedparser)
    # ─────────────────────────────────────────────

    def _parse_with_xml(self, url: str, source: str) -> List[NewsItem]:
        """Parse RSS/Atom using stdlib xml.etree — handles namespaces via stripping."""
        import xml.etree.ElementTree as ET

        content = self._http_get(url)

        # Strip XML namespaces so we can find tags by simple names
        content_clean = re.sub(r' xmlns[^"]*"[^"]*"', "", content)
        content_clean = re.sub(r'<([a-z]+):[a-zA-Z]', lambda m: "<" + m.group(0)[1:].split(":")[1], content_clean)

        try:
            root = ET.fromstring(content_clean)
        except ET.ParseError:
            # Last resort: regex extraction of <title> tags
            return self._parse_with_regex(content, source, url)

        items = []
        cutoff = datetime.now() - timedelta(hours=self.MAX_AGE_HOURS)

        # Handle both RSS (<item>) and Atom (<entry>) formats
        xml_items = list(root.iter("item")) or list(root.iter("entry"))
        if not xml_items:
            raise RuntimeError("No <item> or <entry> elements found in feed")

        for node in xml_items:
            title = (node.findtext("title") or "").strip()
            # Remove CDATA wrappers if present
            title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title).strip()
            link  = (node.findtext("link") or node.findtext("url") or "").strip()
            pub   = (node.findtext("pubDate") or node.findtext("published") or
                     node.findtext("updated") or "").strip()

            if not title:
                continue

            ts = self._parse_rss_date(pub)
            if ts < cutoff:
                ts = datetime.now()   # keep it but mark as now (don't skip)

            scored = self._score_title(title, source, link, ts)
            if scored:
                items.append(scored)

        return items

    # ─────────────────────────────────────────────
    # PARSER 3 — regex last-resort
    # ─────────────────────────────────────────────

    def _parse_with_regex(self, content: str, source: str, url: str) -> List[NewsItem]:
        """Last-resort: extract titles using regex when XML parsing fails."""
        titles = re.findall(r"<title[^>]*><!\[CDATA\[(.*?)\]\]></title>", content, re.DOTALL)
        if not titles:
            titles = re.findall(r"<title[^>]*>(.*?)</title>", content, re.DOTALL)

        # Skip the first title (usually the feed name, not an article)
        items = []
        for title in titles[1:21]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            if title:
                scored = self._score_title(title, source, url, datetime.now())
                if scored:
                    items.append(scored)
        return items

    # ─────────────────────────────────────────────
    # HTTP FETCH
    # ─────────────────────────────────────────────

    def _http_get(self, url: str, timeout: int = 12) -> str:
        """
        Fetch URL with requests (preferred) or urllib fallback.
        Returns decoded string content.  Raises on failure.
        """
        last_error = None

        # Attempt 1: requests library
        try:
            import requests as req_lib
            resp = req_lib.get(url, headers=_HEADERS, timeout=timeout, verify=True)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code in (301, 302, 307, 308):
                # Follow redirect manually with new URL
                new_url = resp.headers.get("Location", url)
                resp2 = req_lib.get(new_url, headers=_HEADERS, timeout=timeout, verify=True)
                if resp2.status_code == 200:
                    return resp2.text
            # Non-200 but we have content — try to use it anyway (some feeds return 206)
            if resp.text and len(resp.text) > 200:
                return resp.text
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)

        # Attempt 2: urllib with SSL bypass (handles some cert issues)
        try:
            import urllib.request
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            last_error = str(e)

        raise ConnectionError(f"Both requests and urllib failed for {url}: {last_error}")

    # ─────────────────────────────────────────────
    # DATE PARSING
    # ─────────────────────────────────────────────

    def _parse_rss_date(self, pub_str: str) -> datetime:
        """Parse RFC-2822 / ISO 8601 date string to naive local datetime."""
        if not pub_str:
            return datetime.now()
        try:
            dt_aware = parsedate_to_datetime(pub_str)
            return dt_aware.astimezone().replace(tzinfo=None)
        except Exception:
            pass
        # ISO 8601 format (Atom feeds)
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                s = pub_str[:25].strip()
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo:
                    return dt.astimezone().replace(tzinfo=None)
                return dt
            except Exception:
                pass
        return datetime.now()

    # ─────────────────────────────────────────────
    # TITLE SCORER
    # ─────────────────────────────────────────────

    def _score_title(self, title: str, source: str, link: str, ts: datetime) -> Optional[NewsItem]:
        """Score a headline and return a NewsItem (or None if score=0 and unimpactful)."""
        if not title.strip():
            return None

        title_lower = title.lower()
        score = 0.0
        keywords_found = []

        # Use longest-match priority: if a longer compound keyword matches,
        # don't also count its single-word substrings (prevents double-counting).
        matched_spans: set = set()

        def _add_kw(kw: str, weight: float, label: str):
            nonlocal score
            pos = title_lower.find(kw)
            while pos != -1:
                span = (pos, pos + len(kw))
                # For single-word keywords, require word boundaries to avoid
                # matching substrings (e.g. "gains" inside "against")
                is_word_boundary = True
                if " " not in kw:   # single-word keyword
                    before = title_lower[pos - 1] if pos > 0 else " "
                    after  = title_lower[pos + len(kw)] if pos + len(kw) < len(title_lower) else " "
                    is_word_boundary = (not before.isalpha()) and (not after.isalpha())
                if is_word_boundary:
                    # Skip if overlapped by an already-matched longer compound keyword
                    if not any(s[0] <= span[0] < s[1] or s[0] < span[1] <= s[1]
                               for s in matched_spans if s[1] - s[0] > len(kw)):
                        matched_spans.add(span)
                        score += weight
                        keywords_found.append(label)
                        break
                pos = title_lower.find(kw, pos + 1)

        # Sort by keyword length (longest first) so compound phrases take priority
        for kw, weight in sorted(BULLISH_KEYWORDS.items(), key=lambda x: -len(x[0])):
            _add_kw(kw, weight, f"+{kw}")
        for kw, weight in sorted(BEARISH_KEYWORDS.items(), key=lambda x: -len(x[0])):
            _add_kw(kw, weight, kw)

        score = max(-10, min(10, score))
        is_hi = any(ev in title_lower for ev in HIGH_IMPACT_EVENTS)

        # Include even neutral items if high-impact; discard pure zeros
        if score == 0 and not is_hi:
            return None

        return NewsItem(
            title          = title.strip(),
            source         = source,
            url            = link,
            timestamp      = ts,
            score          = score,
            is_high_impact = is_hi,
            keywords_found = keywords_found[:5],
        )


# ─────────────────────────────────────────────
# SIGNAL HELPER (for signal_aggregator.py)
# ─────────────────────────────────────────────

def get_news_signal(analyzer: NewsSentimentAnalyzer) -> Dict:
    """
    Returns a simple dict for use in signal aggregation.
    Contributes up to 5 points in the scoring system.
    """
    try:
        result = analyzer.get_sentiment()
        score  = result.overall_score

        if result.signal == "BULLISH":
            pts = min(score / 2, 5)
            direction = "BULL"
        elif result.signal == "BEARISH":
            pts = max(score / 2, -5)
            direction = "BEAR"
        else:
            pts = 0
            direction = "NEUTRAL"

        hi_impact_str = ""
        if result.high_impact_news:
            hi_impact_str = " | ⚠️  " + result.high_impact_news[0].title[:60]

        return {
            "signal":        direction,
            "score":         result.overall_score,
            "points":        round(pts, 1),
            "confidence":    result.confidence,
            "high_impact":   bool(result.high_impact_news),
            "summary":       f"News:{direction}({result.overall_score:+.1f}){hi_impact_str}",
            "headlines":     [i.title for i in result.recent_headlines[:3]],
            "source_scores": result.source_scores,
            "error":         result.error,
        }
    except Exception as e:
        logger.warning(f"[News] get_news_signal failed: {e}")
        return {
            "signal": "NEUTRAL", "score": 0.0, "points": 0.0,
            "confidence": 0.0, "high_impact": False,
            "summary": "News:NEUTRAL(unavailable)", "headlines": [],
            "source_scores": {}, "error": str(e),
        }


# ─────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("\n=== News Sentiment Test ===\n")
    analyzer = NewsSentimentAnalyzer()
    result   = analyzer.get_sentiment(force_refresh=True)

    print(f"Overall Score  : {result.overall_score:+.2f} / 10")
    print(f"Signal         : {result.signal}")
    print(f"Confidence     : {result.confidence:.1f}%")
    print(f"Last Updated   : {result.last_updated}")
    if result.error:
        print(f"Error          : {result.error}")

    print(f"\nSource Scores:")
    for src, sc in result.source_scores.items():
        bar = "█" * int(abs(sc)) if abs(sc) >= 1 else "·"
        print(f"  {src:22} {sc:+.2f}  {bar}")

    if result.high_impact_news:
        print(f"\n⚠️  HIGH IMPACT NEWS:")
        for item in result.high_impact_news:
            print(f"  [{item.source}] {item.title[:80]}")

    print(f"\nRecent Headlines ({len(result.recent_headlines)} items):")
    for item in result.recent_headlines[:5]:
        icon = "📈" if item.score > 0 else "📉" if item.score < 0 else "➖"
        print(f"  {icon} [{item.source}] {item.title[:70]}  (score: {item.score:+.1f})")
