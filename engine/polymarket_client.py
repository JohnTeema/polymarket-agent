"""
Thin wrapper over the Gamma + CLOB + Data APIs.
Read-only, no auth needed.
"""
import json
import urllib.parse
import requests
from typing import Any

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

_HEADERS = {"User-Agent": "polymarket-agent/0.1"}


def _get(url: str) -> Any:
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def search_markets(query: str, limit: int = 20) -> list[dict]:
    """Search for markets matching a query string."""
    data = _get(f"{GAMMA}/public-search?q={urllib.parse.quote(query)}&limit={limit}")
    return data.get("events", [])


def get_active_events(limit: int = 200) -> list[dict]:
    """Get active (open) events sorted by volume. Single source of truth."""
    try:
        return _get(
            f"{GAMMA}/events?limit={limit}&active=true&closed=false&order=volume&ascending=false"
        )
    except Exception as e:
        print(f"  [get_active_events] failed: {e}")
        return []


def get_crypto_updown_markets() -> list[dict]:
    """
    Fetch 'up or down' crypto price markets via two Gamma API calls:
    1. Slug search for the canonical bitcoin-up-or-down event
    2. Crypto-tagged events sorted by 24hr volume
    Extracts nested markets, filters for 'up or down' in the question.
    Returns a flat list of market dicts.
    """
    raw_events: list[dict] = []

    try:
        raw_events += _get(
            f"{GAMMA}/events?slug=bitcoin-up-or-down&active=true&closed=false&limit=10"
        )
    except Exception as e:
        print(f"  [updown] slug search failed: {e}")

    try:
        raw_events += _get(
            f"{GAMMA}/events?active=true&closed=false&limit=50&tag=crypto&order=volume24hr&ascending=false"
        )
    except Exception as e:
        print(f"  [updown] tag search failed: {e}")

    markets: list[dict] = []
    seen: set = set()
    for evt in raw_events:
        for m in evt.get("markets", []):
            if "up or down" not in m.get("question", "").lower():
                continue
            mid = m.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                markets.append(m)

    print(f"  [updown] found {len(markets)} 'up or down' markets")
    for m in markets[:3]:
        print(f"  [updown]   · {m.get('question', '?')}")
    return markets


_DAILY_KEYWORDS = {"up or down", "hourly", "daily"}

def get_crypto_daily_markets(limit: int = 200) -> list[dict]:
    """
    Two-call approach to find short-term crypto price markets:
    1. Crypto events endpoint (tag=crypto, by 24hr volume) — extracts nested markets
    2. All markets endpoint (high volume) — keyword filtered for daily price questions
    Combines and deduplicates by market ID. Returns flat market list.
    """
    # Call 1: crypto-tagged events, extract their nested markets
    from_events: list[dict] = []
    try:
        events = _get(
            f"{GAMMA}/events?tag=crypto&active=true&closed=false&limit=50&order=volume24hr&ascending=false"
        )
        for evt in events:
            for m in evt.get("markets", []):
                if m.get("active") and not m.get("closed"):
                    from_events.append(m)
    except Exception as e:
        print(f"  [crypto_daily] events call failed: {e}")

    # Call 2: all high-volume markets, keyword filtered
    from_search: list[dict] = []
    try:
        all_markets = _get(
            f"{GAMMA}/markets?active=true&closed=false&limit={limit}&order=volume24hr&ascending=false"
        )
        for m in all_markets:
            q = m.get("question", "").lower()
            if any(kw in q for kw in _DAILY_KEYWORDS):
                from_search.append(m)
    except Exception as e:
        print(f"  [crypto_daily] markets call failed: {e}")

    # Combine and deduplicate by ID
    seen: set = set()
    combined: list[dict] = []
    for m in from_events + from_search:
        mid = m.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            combined.append(m)

    print(f"  [crypto_daily] events={len(from_events)} keyword={len(from_search)} combined={len(combined)} unique")
    for m in combined[:5]:
        print(f"  [crypto_daily]   · {m.get('question', '?')}")
    return combined


def get_daily_crypto_markets(limit: int = 100) -> list[dict]:
    """
    Fetch two slices of markets — soonest-resolving and highest 24h volume —
    deduplicate by market ID, and return the combined list.
    Filtering by 24h window and min volume is handled by filter_candidate_markets.
    """
    soonest = _get(
        f"{GAMMA}/markets?active=true&closed=false&order=end_date&ascending=true&limit={limit}"
    )
    trending = _get(
        f"{GAMMA}/markets?active=true&closed=false&order=volume24hr&ascending=false&limit={limit}"
    )

    seen = set()
    combined = []
    for m in soonest + trending:
        mid = m.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            combined.append(m)

    print(f"  [daily_crypto] soonest={len(soonest)} trending={len(trending)} combined={len(combined)} unique")
    for m in combined[:3]:
        print(f"  [daily_crypto]   · {m.get('question', '?')}")
    return combined


def trending_events(limit: int = 20) -> list[dict]:
    """Get top events by volume."""
    return _get(
        f"{GAMMA}/events?limit={limit}&active=true&closed=false&order=volume&ascending=false"
    )


def get_market(slug: str) -> dict | None:
    """Get a single market by slug."""
    markets = _get(f"{GAMMA}/markets?slug={urllib.parse.quote(slug)}")
    return markets[0] if markets else None


def get_market_by_id(market_id: str) -> dict | None:
    """Get a single market by its ID."""
    result = _get(f"{GAMMA}/markets/{market_id}")
    if isinstance(result, dict):
        return result
    if isinstance(result, list) and result:
        return result[0]
    return None


def get_price(token_id: str, side: str = "buy") -> dict:
    """Get current price for a token (Yes/No)."""
    return _get(f"{CLOB}/price?token_id={token_id}&side={side}")


def get_orderbook(token_id: str) -> dict:
    """Get bid/ask stack for a token."""
    return _get(f"{CLOB}/book?token_id={token_id}")


def get_price_history(condition_id: str, interval: str = "all", fidelity: int = 100) -> list[dict]:
    """Get historical price data for a market."""
    data = _get(
        f"{CLOB}/prices-history?market={condition_id}&interval={interval}&fidelity={fidelity}"
    )
    return data.get("history", [])


def get_recent_trades(market: str | None = None, limit: int = 50) -> list[dict]:
    """Get recent trades, optionally filtered by market."""
    url = f"{DATA}/trades?limit={limit}"
    if market:
        url += f"&market={market}"
    return _get(url)


def _parse_double_json(val):
    """Parse double-encoded JSON strings from Gamma API."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def format_market(m: dict) -> dict:
    """Normalize a raw market response into a clean dict."""
    prices = _parse_double_json(m.get("outcomePrices", "[]"))
    outcomes = _parse_double_json(m.get("outcomes", "[]"))
    tokens = _parse_double_json(m.get("clobTokenIds", "[]"))

    return {
        "id": m.get("id"),
        "question": m.get("question", "?"),
        "slug": m.get("slug", ""),
        "condition_id": m.get("conditionId", ""),
        "active": m.get("active", False),
        "closed": m.get("closed", False),
        "end_date": m.get("endDate"),
        "volume": float(m.get("volume", 0) or 0),
        "liquidity": float(m.get("liquidity", 0) or 0),
        "category": m.get("category", ""),
        "yes_price": float(prices[0]) if isinstance(prices, list) and len(prices) >= 1 else None,
        "no_price": float(prices[1]) if isinstance(prices, list) and len(prices) >= 2 else None,
        "outcomes": outcomes if isinstance(outcomes, list) else ["Yes", "No"],
        "yes_token": tokens[0] if isinstance(tokens, list) and len(tokens) >= 1 else "",
        "no_token": tokens[1] if isinstance(tokens, list) and len(tokens) >= 2 else "",
        "description": m.get("description", ""),
    }
