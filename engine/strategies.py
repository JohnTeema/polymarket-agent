"""
Trading strategies for crypto and sports markets on Polymarket.
Uses event tags (from Gamma API) to filter, then LLM for decision.
"""
import json
import urllib.request
import urllib.error
import os
import yaml
from pathlib import Path
from packaging.version import parse as _parse

from engine.polymarket_client import (
    format_market, get_market, get_orderbook,
    get_recent_trades, get_price_history, _parse_double_json, _get,
)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

# ── Tag taxonomy ─────────────────────────────────────────────
SPORTS_TAGS = {
    "sports", "soccer", "football", "nba", "nba-champion", "nba-finals", "nba-playoffs",
    "nfl", "baseball", "mlb", "mlb-playoffs", "mls", "nhl", "nhl-playoffs",
    "f1", "formula1", "super-bowl", "tennis", "atp", "pga", "pga-championship",
    "golf", "premier-league", "champions-league", "fifa-world-cup", "ucl",
    "2026-fifa-world-cup", "major-championships", "stanley-cup", "world-series",
    "roland-garros", "epr",
}
CRYPTO_TAGS = {
    "crypto", "crypto-prices", "bitcoin", "microstrategy",
    "token-launch", "token-sales", "solana", "ethereum", "defi",
}
EXCLUDED_TAGS = {
    "politics", "geopolitics", "elections", "world-elections", "global-elections",
    "president", "primaries", "primary-elections", "main-election",
    "us-presidential-election", "french-election", "brazil",
    "iranian-leadership-regime", "maduro", "putin", "ukraine", "russia",
    "china", "middle-east", "us-iran", "trump", "trump-presidency", "khamenei",
    "world-affairs", "diplomacy-ceasefire", "tariffs",
    "commodities", "oil", "nymex-crude-oil-futures",
}


# ── Config helpers ───────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


# ── Helpers ──────────────────────────────────────────────────
def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_vol(v) -> str:
    try:
        v = float(v)
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        if v >= 1_000: return f"${v/1_000:.1f}K"
        return f"${v:.0f}"
    except: return "?"


# ── Tag-based event classifier ─────────────────────────────────
def _event_tags(evt) -> set:
    return {t["slug"] for t in evt.get("tags", []) if "slug" in t}


def _classify_evt(evt) -> str | None:
    """Returns 'crypto', 'sports', or None (skip)."""
    tags = _event_tags(evt)
    overlap_excl = tags & EXCLUDED_TAGS
    if overlap_excl:
        return None
    overlap_crypto = tags & CRYPTO_TAGS
    if overlap_crypto:
        return "crypto"
    overlap_sports = tags & SPORTS_TAGS
    if overlap_sports:
        return "sports"
    return None


# ── Market filtering ──────────────────────────────────────────
def filter_candidate_markets(events: list[dict], min_volume: float = 50_000) -> list[tuple[dict, str]]:
    """
    Return [(market, mm_type)] for active non-closed markets in crypto/sports
    with volume ≥ min_volume. Uses event-level tags for classification.
    """
    candidates = []
    seen_slugs = set()
    for evt in events:
        mm_type = _classify_evt(evt)
        if mm_type is None:
            continue
        for m in evt.get("markets", []):
            if not m.get("active") or m.get("closed"):
                continue
            vol = float(m.get("volume", 0) or 0)
            if vol < min_volume:
                continue
            slug = m.get("slug", "")
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            candidates.append((m, mm_type))
    return candidates


# ── Market context for LLM ───────────────────────────────────
def build_market_context(market: dict, mm_type: str = "crypto") -> str:
    fm = format_market(market)
    type_hint = "CRYPTO market" if mm_type == "crypto" else "SPORTS market"
    try:
        history = get_price_history(fm["condition_id"], interval="1m", fidelity=50)
        orderbook = get_orderbook(fm["yes_token"])
        trades = get_recent_trades(market=fm["condition_id"], limit=10)
    except Exception as e:
        history = []
        orderbook = {}
        trades = []

    trend = ""
    if history:
        prices = [float(h["p"]) for h in history]
        n = len(prices)
        if n >= 6:
            early = sum(prices[:n//3]) / (n//3)
            late  = sum(prices[-n//3:]) / (n//3)
            direction = "RISING" if late > early else ("FALLING" if late < early else "FLAT")
            trend = f"Price trend (1m): {direction}  {_fmt_pct(early)} → {_fmt_pct(late)}"

    ob = ""
    if orderbook.get("bids"):
        bid  = orderbook["bids"][0]["price"]
        ask  = orderbook["asks"][0]["price"] if orderbook.get("asks") else "?"
        ob   = f"Orderbook top: bid={_fmt_pct(float(bid))}  ask={_fmt_pct(float(ask))}"

    end = (fm["end_date"] or "")[:10]
    return f"""
MARKET: {fm['question']}
Current odds: Yes={_fmt_pct(fm['yes_price'])}  No={_fmt_pct(fm['no_price'])}  (vol {_fmt_vol(fm['volume'])})
Ends: {end}
{ob}
{trend}
Recent trades: {len(trades)}

ANALYSIS GUIDE:
1. Is this outcome rule-bound and rigorous, or chaotic/unpredictable?
2. Where is the real edge — institutional bias, lazy markets, slow-moving news?
3. What domain knowledge (chain, sport, policy) makes me more confident than the crowd?
4. What would make the market resolve differently from what I believe?
{type_hint}
""".strip()


# ── Decision engine ──────────────────────────────────────────
def make_trading_decision(market: dict, mm_type: str) -> dict | None:
    """
    Analyze a market and return a structured decision dict:
    {action, confidence, market_price, edge, rationale, risk}
    or None if no trade.
    """
    from engine.ledger import get_open_positions

    cfg = load_config()
    api_key = os.environ.get("OPENAI_API_KEY", "") or cfg.get("openai_api_key", "")
    max_open = cfg.get("max_open_positions", 5)
    if len(get_open_positions()) >= max_open:
        return None

    fm = format_market(market)
    context = build_market_context(market, mm_type=mm_type)
    type_hint = "DOMAIN: Crypto — use price levels, funding, macro, on-chain flows" \
        if mm_type == "crypto" else \
        "DOMAIN: Sports — use team form, injuries, matchups, schedule, rest"

    prompt = f"""You are a sharp prediction market trader.
{type_hint}

{context}

TASK:
1. Is this a tradeable opportunity with a real edge versus the crowd?
2. If the market price is 0.55 but you assign 0.65 true probability → gap is YOUR edge.
3. Do NOT assume you have data you don't have — if the crowd just knows something you
   don't, PASS.
4. Stake (confidence) should only be high when you have a structural reason to disagree
   with the market's implied probability — not because you "feel" one way.
5. Risk: "low" = narrow edge, high confidence | "medium" = moderate gap | "high" = high gap

OUTPUT FORMAT — reply ONLY with this exact JSON:
{{"action": "trade_yes"|"trade_no"|"pass",
  "confidence": 0.0-1.0,
  "rationale": "2–3 sentences",
  "risk": "low"|"medium"|"high",
  "edge": 0.0-1.0}}"""

    # --- LLM path ---
    base_url = cfg.get("openai_base_url", "https://api.openai.com/v1").rstrip("/")
    base_url = cfg.get("openai_base_url", "https://api.openai.com/v1").rstrip("/")
    is_openai    = api_key.startswith("sk-")
    is_freemodel  = api_key.startswith("fe_oa_")
    if api_key and (is_openai or is_freemodel):
            payload = json.dumps({
            "model": cfg.get("llm_model", "gpt-4o"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 350,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        raw = raw.strip().removeprefix("```json").removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        d = json.loads(raw)
        except Exception as e:
        print(f"  [LLM ERROR] {e}. Falling back to heuristic.")
        d = _heuristic(fm)
    else:
        d = _heuristic(fm)

    d["market_price"] = fm["yes_price"]
    if not isinstance(d.get("confidence"), float):
        d["confidence"] = float(d.get("confidence", fm["yes_price"]))
    d["edge"] = max(float(d.get("edge", 0.0)), 0.0)
    return d


def _heuristic(fm: dict) -> dict:
    """When no LLM is available, bet against overpriced thin markets."""
    yes = fm["yes_price"]
    vol = fm["volume"]
    if yes > 0.80 and vol < 200_000:
        return {
            "action": "trade_no",
            "confidence": 0.58,
            "market_price": yes,
            "edge": yes - 0.58,
            "rationale": f"Heuristic: Yes {_fmt_pct(yes)} with thin vol — betting No.",
            "risk": "medium",
        }
    if yes < 0.20 and vol < 200_000:
        return {
            "action": "trade_yes",
            "confidence": 0.58,
            "market_price": yes,
            "edge": 0.52 - yes,
            "rationale": f"Heuristic: Yes {_fmt_pct(yes)} with thin vol — betting Yes.",
            "risk": "medium",
        }
    return {
        "action": "pass",
        "confidence": yes,
        "market_price": yes,
        "edge": 0.0,
        "rationale": "Heuristic: No clear edge without LLM.",
        "risk": "low",
    }