"""
Trading strategies for crypto and sports markets on Polymarket.
Uses event tags (from Gamma API) to filter, then LLM for decision.
"""
import json
import logging
import time
import requests
import os
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

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


# ── NBA team name lookup ─────────────────────────────────────
_NBA_TEAMS = {
    "spurs": "San Antonio Spurs",       "thunder": "Oklahoma City Thunder",
    "lakers": "Los Angeles Lakers",     "warriors": "Golden State Warriors",
    "celtics": "Boston Celtics",        "heat": "Miami Heat",
    "nuggets": "Denver Nuggets",        "timberwolves": "Minnesota Timberwolves",
    "wolves": "Minnesota Timberwolves", "knicks": "New York Knicks",
    "pacers": "Indiana Pacers",         "bucks": "Milwaukee Bucks",
    "sixers": "Philadelphia 76ers",     "76ers": "Philadelphia 76ers",
    "cavaliers": "Cleveland Cavaliers", "cavs": "Cleveland Cavaliers",
    "magic": "Orlando Magic",           "hawks": "Atlanta Hawks",
    "nets": "Brooklyn Nets",            "raptors": "Toronto Raptors",
    "bulls": "Chicago Bulls",           "pistons": "Detroit Pistons",
    "hornets": "Charlotte Hornets",     "wizards": "Washington Wizards",
    "clippers": "Los Angeles Clippers", "suns": "Phoenix Suns",
    "kings": "Sacramento Kings",        "jazz": "Utah Jazz",
    "blazers": "Portland Trail Blazers","rockets": "Houston Rockets",
    "grizzlies": "Memphis Grizzlies",   "pelicans": "New Orleans Pelicans",
    "mavericks": "Dallas Mavericks",    "mavs": "Dallas Mavericks",
}


def get_live_context(question: str, mm_type: str = "sports") -> str:
    """
    Fetch live game data for the market if available.
    Always returns today's date and a reminder not to invent stats.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"Today's date: {today}"]
    lines.append(
        "INTEGRITY RULE: DO NOT invent statistics or scores. Only reference facts "
        "you are confident about from your training data. If unsure of a specific "
        "stat, omit it rather than guessing."
    )

    q_lower = question.lower()

    _crypto_price_keywords = ("bitcoin", "btc", "ethereum", "eth", "price of")
    if mm_type != "sports":
        if any(kw in q_lower for kw in _crypto_price_keywords):
            try:
                resp = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    parts = []
                    if "bitcoin" in data:
                        parts.append(f"Bitcoin = ${data['bitcoin']['usd']:,.0f}")
                    if "ethereum" in data:
                        parts.append(f"Ethereum = ${data['ethereum']['usd']:,.0f}")
                    if "solana" in data:
                        parts.append(f"Solana = ${data['solana']['usd']:,.0f}")
                    if parts:
                        lines.append("LIVE PRICE: " + " | ".join(parts))
                else:
                    lines.append(f"(CoinGecko API returned {resp.status_code})")
            except Exception as e:
                lines.append(f"(Live crypto price unavailable: {e})")
        return "\n".join(lines)


    matched = [alias for alias in _NBA_TEAMS if alias in q_lower]

    if not matched:
        return "\n".join(lines)

    # Try balldontlie free API for today's live scores
    try:
        resp = requests.get(
            f"https://www.balldontlie.io/api/v1/games?dates[]={today}",
            timeout=5,
        )
        if resp.status_code == 200:
            games = resp.json().get("data", [])
            for game in games:
                home = game["home_team"]["full_name"].lower()
                visitor = game["visitor_team"]["full_name"].lower()
                if any(alias in home or alias in visitor for alias in matched):
                    status = game.get("status", "scheduled")
                    h_score = game.get("home_team_score") or 0
                    v_score = game.get("visitor_team_score") or 0
                    period = game.get("period") or 0
                    lines.append(
                        f"LIVE SCORE: {game['visitor_team']['full_name']} {v_score} "
                        f"@ {game['home_team']['full_name']} {h_score} "
                        f"| Status: {status} | Period: {period}"
                    )
        else:
            lines.append(f"(Live score API returned {resp.status_code})")
    except Exception as e:
        lines.append(f"(Live score unavailable: {e})")

    lines.append(
        "Series context: Use your training data for playoff round, series record, "
        "home/away splits, and recent game results."
    )
    return "\n".join(lines)


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
    Return [(market, mm_type)] sorted by volume descending, for active non-closed
    markets in crypto/sports with volume ≥ min_volume that resolve within 48 hours.
    Excludes markets with Yes price below 2% or above 98% (effectively decided).
    """
    _CRYPTO_KW = (
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
        "crypto", "xrp", "dogecoin", "doge", "price of", "up or down",
    )
    _FOOTBALL_KW = (
        "premier league", "champions league", "world cup", "fifa",
        "epl", "la liga", "serie a", "bundesliga", "ligue 1",
        "europa league", "arsenal", "manchester", "liverpool",
        "chelsea", "tottenham", "barcelona", "real madrid", "bayern",
        "psg", "juventus", "inter milan", "ac milan", "man city",
        "man utd",
    )

    candidates = []
    seen_slugs = set()
    now = datetime.now(timezone.utc)
    crypto_cutoff = now + timedelta(hours=48)
    football_cutoff = now + timedelta(days=30)

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
            fm = format_market(m)
            end_str = fm.get("end_date") or ""
            if not end_str:
                continue
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            question = (m.get("question") or "").lower()
            is_football = any(kw in question for kw in _FOOTBALL_KW)
            is_crypto = any(kw in question for kw in _CRYPTO_KW)
            if not is_football and not is_crypto:
                logger.info("[FILTER] Skipping non-crypto/football market: %s", m.get("question", ""))
                continue
            market_cutoff = football_cutoff if is_football else crypto_cutoff
            if end_dt > market_cutoff:
                continue
            yes_price = fm.get("yes_price")
            if yes_price is None or yes_price < 0.02 or yes_price > 0.98:
                continue
            seen_slugs.add(slug)
            candidates.append((m, mm_type))

    candidates.sort(key=lambda x: float(x[0].get("volume", 0) or 0), reverse=True)
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
        try:
            bid = float(orderbook["bids"][0]["price"])
        except (ValueError, TypeError):
            bid = 0.0
        try:
            ask = float(orderbook["asks"][0]["price"]) if orderbook.get("asks") else 0.0
        except (ValueError, TypeError):
            ask = 0.0
        if not (bid < 0.02 and ask > 0.98):
            ob = f"Orderbook top: bid={_fmt_pct(bid)}  ask={_fmt_pct(ask)}"

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
    live_context = get_live_context(fm["question"], mm_type=mm_type)
    prompt = f"""You are a prediction market trader. You have knowledge about sports (NBA, NHL, NFL, MLB, soccer, F1, tennis), crypto markets, politics, esports, and general events. Use ALL your knowledge to find edge in any market type. Don't restrict yourself to one domain.

{context}

LIVE CONTEXT:
{live_context}

KNOWLEDGE RULES:
1. You have training data on sports teams, players, season stats, and recent performance. USE it.
   Do not say "I don't have enough information" — you have general knowledge about these matchups.
2. For games happening today: factor in team season records, key players, home/away advantage,
   playoff context, rest days, and scoring/defensive trends.
3. A confidence of 0.60 is enough to trade — you do NOT need 90% certainty. Be willing to take
   positions when you see value.
4. Look for specific edges: is a spread too wide for a team's actual form? Is an over/under
   ignoring a team's pace or scoring trends? Is a heavy favourite being underpriced?
5. If the market price is 0.55 but you assign 0.65 true probability → that gap IS your edge. Trade it.
6. NEVER trade a market priced below 1% or above 99% — these are resolved or have no liquidity. action must be "pass".

TASK:
1. Is this a tradeable opportunity with a real edge versus the crowd?
2. Use your knowledge of the teams, players, and context to form a concrete opinion.
3. Risk: "low" = narrow edge, high confidence | "medium" = moderate gap | "high" = large gap

OUTPUT FORMAT — reply ONLY with this exact JSON:
{{"action": "trade_yes"|"trade_no"|"pass",
  "confidence": 0.0-1.0,
  "rationale": "2–3 sentences citing specific knowledge about the teams or matchup",
  "risk": "low"|"medium"|"high",
  "edge": 0.0-1.0}}"""

    # --- LLM path ---
    base_url = cfg.get("openai_base_url", "https://api.openai.com/v1").rstrip("/")
    is_openai    = api_key.startswith("sk-")
    is_freemodel = api_key.startswith("fe_oa_")
    is_groq      = api_key.startswith("gsk_")
    if api_key and (is_openai or is_freemodel or is_groq):
        try:
            print(f"  [LLM URL] {base_url}/chat/completions")
            print(f"  [LLM AUTH] Bearer {api_key[:8]}{'*' * (len(api_key) - 8)}")
            _llm_payload = {
                "model": cfg.get("llm_model", "gpt-4o"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 350,
            }
            _llm_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers=_llm_headers,
                json=_llm_payload,
                timeout=30,
            )
            if resp.status_code == 429:
                print("  [LLM] 429 rate limit — waiting 10s and retrying once...")
                time.sleep(10)
                resp = requests.post(
                    f"{base_url}/chat/completions",
                    headers=_llm_headers,
                    json=_llm_payload,
                    timeout=30,
                )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
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