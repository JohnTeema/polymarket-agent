"""
Thin wrapper over the Gamma + CLOB + Data APIs.
Read-only, no auth needed.
"""
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Any

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-agent/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def search_markets(query: str, limit: int = 20) -> list[dict]:
    """Search for markets matching a query string."""
    data = _get(f"{GAMMA}/public-search?q={urllib.parse.quote(query)}&limit={limit}")
    return data.get("events", [])


def get_active_events(limit: int = 50) -> list[dict]:
    """Get active (open) events sorted by volume. Single source of truth."""
    return _get(
        f"{GAMMA}/events?limit={limit}&active=true&closed=false&order=volume&ascending=false"
    )


def trending_events(limit: int = 20) -> list[dict]:
    """Get top events by volume."""
    return _get(
        f"{GAMMA}/events?limit={limit}&active=true&closed=false&order=volume&ascending=false"
    )


def get_market(slug: str) -> dict | None:
    """Get a single market by slug."""
    markets = _get(f"{GAMMA}/markets?slug={urllib.parse.quote(slug)}")
    return markets[0] if markets else None


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
