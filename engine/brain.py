"""
Agent orchestrator — main loop.
1. Scan markets → 2. Analyze → 3. Decide → 4. Execute (paper) → 5. Await settlement
"""
import re
import sys
import os
import json as _json
from pathlib import Path
from datetime import datetime
from engine.ledger import (
    open_position, close_position, settle_position, get_open_positions, get_summary, get_all_positions
)
from engine.polymarket_client import (
    get_active_events, get_daily_crypto_markets, get_crypto_daily_markets,
    get_crypto_updown_markets, format_market, get_orderbook, get_price_history,
    get_market_by_id,
)
from engine.strategies import make_trading_decision, load_config, filter_candidate_markets


AGENT_LOG = Path(__file__).parent.parent / "data" / "agent_log.jsonl"

import sys
import json
from pathlib import Path
try:
    from engine.alerts import send_telegram_message
except Exception:
    def send_telegram_message(msg: str, level: str = "info"):
        pass  # fallback if alerts module is broken


def log(msg: str):
    ts = datetime.utcnow().isoformat()
    line = _json.dumps({"ts": ts, "msg": msg})
    with open(AGENT_LOG, "a") as f:
        f.write(line + "\n")
    print(f"[{ts[:19]}] {msg}")


PROFIT_TARGET = 0.15   # close if price is 15% above entry
STOP_LOSS     = 0.20   # close if price is 20% below entry


def _get_current_price(pos: dict) -> float | None:
    """Fetch current price from the Gamma API market endpoint using outcomePrices."""
    try:
        market = get_market_by_id(pos["market_id"])
        if not market:
            return None
        raw = market.get("outcomePrices", "[]")
        prices = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(prices, list) or len(prices) < 2:
            return None
        yes_price = float(prices[0])
        return yes_price if pos["outcome"] == "yes" else float(prices[1])
    except Exception:
        return None


def check_exit_opportunities():
    """Check all open positions for profit target or stop-loss triggers and close if hit."""
    open_pos = get_open_positions()
    if not open_pos:
        return
    log(f"Checking exit opportunities for {len(open_pos)} open position(s)...")
    now = datetime.utcnow()
    for pos in open_pos:
        # Skip positions opened less than 1 hour ago
        try:
            opened_at = datetime.fromisoformat(pos["opened_at"])
            if (now - opened_at).total_seconds() < 3600:
                log(f"  [EXIT CHECK] #{pos['id']} — opened <1h ago, skipping")
                continue
        except Exception:
            pass

        current = _get_current_price(pos)
        if current is None:
            log(f"  [EXIT CHECK] #{pos['id']} — could not fetch price, skipping")
            continue

        # Ignore extreme prices — market is likely resolved; let settle handle it
        if current < 0.02 or current > 0.98:
            log(f"  [EXIT CHECK] #{pos['id']} — price {current*100:.1f}% at extreme, skipping (use settle)")
            continue

        entry = pos["entry_price"]
        pct = (current - entry) / entry
        pnl = round(pos["size_usdc"] * (current - entry) / entry, 2)
        log(
            f"  [EXIT CHECK] #{pos['id']} {pos['outcome'].upper()} | "
            f"entry={entry*100:.1f}% current={current*100:.1f}% | chg={pct*100:+.1f}%"
        )
        if pct >= PROFIT_TARGET:
            log(f"[EXIT] Selling position #{pos['id']} — profit target hit (+{pct*100:.1f}%)")
            close_position(pos["id"], current, pnl, notes="profit target hit")
        elif pct <= -STOP_LOSS:
            log(f"[EXIT] Selling position #{pos['id']} — stop loss hit ({pct*100:.1f}%)")
            close_position(pos["id"], current, pnl, notes="stop loss hit")


def settle_positions():
    """Fetch resolved market data for all open positions and settle wins/losses."""
    open_pos = get_open_positions()
    if not open_pos:
        log("No open positions to settle.")
        return
    log(f"Checking settlement for {len(open_pos)} open position(s)...")
    for pos in open_pos:
        try:
            market = get_market_by_id(pos["market_id"])
        except Exception as e:
            log(f"  [SETTLE] #{pos['id']} — could not fetch market: {e}")
            continue

        if not market:
            log(f"  [SETTLE] #{pos['id']} — market not found")
            continue

        fm = format_market(market)

        if not fm.get("closed"):
            log(f"  [SETTLE] #{pos['id']} — still open, skipping")
            continue

        yes_price = fm.get("yes_price")
        if yes_price is None:
            log(f"  [SETTLE] #{pos['id']} — no price data, skipping")
            continue

        yes_won = yes_price > 0.95
        no_won  = yes_price < 0.05
        if not (yes_won or no_won):
            log(f"  [SETTLE] #{pos['id']} — outcome unclear (yes_price={yes_price:.2f}), skipping")
            continue

        outcome_won = (pos["outcome"] == "yes" and yes_won) or (pos["outcome"] == "no" and no_won)
        pnl = pos["size_usdc"] * (1.0 - pos["entry_price"]) if outcome_won else -pos["size_usdc"]

        settle_position(pos["id"], outcome_won, final_price=1.0)

        if outcome_won:
            log(f"[SETTLED] Position #{pos['id']} — WON +${pnl:.2f} | {pos['market_question'][:60]}")
        else:
            log(f"[SETTLED] Position #{pos['id']} — LOST -${pos['size_usdc']:.2f} | {pos['market_question'][:60]}")


def _combine_flat_markets(*market_lists: list[dict]) -> list[dict]:
    """Merge multiple flat market lists, deduplicating by market ID."""
    seen: set = set()
    combined: list[dict] = []
    for markets in market_lists:
        for m in markets:
            mid = m.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                combined.append(m)
    return combined


def _wrap_as_crypto_events(markets: list[dict]) -> list[dict]:
    """Wrap flat market dicts as single-market crypto events for filter_candidate_markets."""
    return [{"tags": [{"slug": "crypto"}], "markets": [m]} for m in markets]


def _merge_candidates(*candidate_lists: list) -> list:
    """Merge (market, mm_type) lists, deduplicating by market ID."""
    seen: set = set()
    merged: list = []
    for clist in candidate_lists:
        for m, mm_type in clist:
            mid = m.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                merged.append((m, mm_type))
            elif not mid:
                merged.append((m, mm_type))
    return merged


def get_all_candidates() -> list:
    """Gather and filter crypto + sports candidates from active events."""
    log("Scanning active events...")
    all_events = get_active_events()
    candidates = filter_candidate_markets(all_events, min_volume=50_000)
    log(f"Found {len(candidates)} candidate markets (min vol $50K)")
    return candidates


def run_scan_only(verbose: bool = False):
    """Just scan and return candidates — no trades."""
    crypto_candidates = []
    try:
        print("[brain] trying daily crypto scan...")
        flat = _combine_flat_markets(get_daily_crypto_markets(), get_crypto_daily_markets(), get_crypto_updown_markets())
        crypto_candidates = filter_candidate_markets(_wrap_as_crypto_events(flat), min_volume=50_000)
        log(f"Crypto scan: {len(crypto_candidates)} candidates from {len(flat)} raw markets")
    except Exception as e:
        log(f"Crypto scan failed ({e}), continuing with active events only")

    event_candidates = get_all_candidates()
    candidates = _merge_candidates(crypto_candidates, event_candidates)
    log(f"Combined: {len(candidates)} total candidates (crypto + football)")

    for m, mm_type in candidates:
        fm = format_market(m)
        print(f"\n  [{mm_type.upper()}] {fm['question']}")
        print(f"    Yes={fm['yes_price']*100:.1f}% No={fm['no_price']*100:.1f}% | vol=${fm['volume']:,.0f}")
    print(f"\nTotal candidates: {len(candidates)}")
    return candidates


_NOISE_WORDS = {
    "the", "will", "vs", "o/u", "nba", "nfl", "mlb", "nhl", "mls",
    "spread", "over", "under", "is", "a", "an", "be", "in", "of",
    "to", "by", "at", "or", "win", "beat", "game", "series", "match",
}

def _game_teams(question: str) -> set[str]:
    """Extract likely team-name tokens from a market question."""
    tokens = set()
    for word in re.split(r'[\s:.,()\/\-]+', question):
        w = word.strip().lower()
        if len(w) > 2 and w not in _NOISE_WORDS and not w[0].isdigit():
            tokens.add(w)
    return tokens

def _implied_winner(question: str, outcome: str) -> str | None:
    """Return the team implied to win by this position, or None if indeterminate."""
    q = question.lower()
    if 'o/u' in q:
        return None  # over/under markets don't imply a winner
    vs = re.split(r'\s+vs\.?\s+', question, maxsplit=1, flags=re.IGNORECASE)
    if len(vs) == 2:
        team_a = vs[0].strip().lower()
        team_b = vs[1].split()[0].rstrip(':.').lower()
        return team_a if outcome == 'yes' else team_b
    m = re.search(r'spread[:\s]+(\w+)', question, re.IGNORECASE)
    if m and outcome == 'yes':
        return m.group(1).lower()
    return None

def _extract_subject_and_range(question: str) -> tuple[str | None, str | None]:
    """
    Detect questions with a numeric range (e.g. '280-299 tweets').
    Returns (normalized_subject, range_str) or (None, None) if no range found.
    """
    m = re.search(r'\b(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\b', question)
    if not m:
        return None, None
    range_str = m.group(0)
    subject = re.sub(r'\b\d[\d,]*\s*[-–]\s*\d[\d,]*\b', '', question)
    subject = re.sub(r'\s+', ' ', subject).lower().strip()
    return subject, range_str


def _conflicts_with_existing(new_q: str, new_outcome: str, open_positions: list) -> tuple[bool, int | None]:
    """Return (True, conflicting_pos_id) if new position contradicts an existing one."""
    new_subj, new_range = _extract_subject_and_range(new_q)
    new_winner = _implied_winner(new_q, new_outcome)
    new_teams = _game_teams(new_q)

    for pos in open_positions:
        ex_q = pos.get('market_question', '')

        # Range conflict: same subject, different number range → mutually exclusive
        if new_subj and new_range:
            ex_subj, ex_range = _extract_subject_and_range(ex_q)
            if ex_subj and ex_range and new_subj == ex_subj and new_range != ex_range:
                return True, pos.get('id')

        # Winner conflict: same game teams, different implied winner
        if new_winner and (new_teams & _game_teams(ex_q)):
            ex_winner = _implied_winner(ex_q, pos.get('outcome', ''))
            if ex_winner and ex_winner != new_winner:
                return True, pos.get('id')

    return False, None


def run_single_trade(market_like: dict, mm_type: str = "crypto"):
    """Run analysis on a single market and execute if decision says trade."""
    log(f"Analyzing: {market_like.get('question', market_like.get('slug', 'unknown'))}")
    decision = make_trading_decision(market_like, mm_type)
    if not decision:
        log("No decision returned")
        return

    print(f"\n  DECISION: {decision['action'].upper()}")
    print(f"  Market price (Yes): {decision['market_price']*100:.1f}%")
    print(f"  Confidence:        {decision['confidence']*100:.1f}%")
    print(f"  Edge:              {decision['edge']*100:.1f}%")
    print(f"  Risk:              {decision['risk']}")
    print(f"  Rationale:         {decision['rationale']}")

    if decision["action"] == "pass":
        log("Decision: PASS — no edge found")
        return

    cfg = load_config()
    starting_capital = cfg.get("paper_capital", 10_000)
    max_per_trade = cfg.get("max_capital_per_trade", 300)
    kelly_frac = cfg.get("kelly_fraction", 0.25)

    # Kelly criterion: f* = (bp - q) / b
    yes_prob = decision["confidence"]
    market_price = decision["market_price"]
    edge = yes_prob - market_price if decision["action"] == "trade_yes" else (1 - yes_prob) - (1 - market_price)

    b = 1  # win odds (binary market pays 1x)
    p = yes_prob if decision["action"] == "trade_yes" else (1 - yes_prob)
    q = 1 - p
    kelly = max((b * p - q) / b, 0)
    kelly_stake = min(kelly * kelly_frac * starting_capital, max_per_trade)

    if kelly_stake < 5:
        print(f"  [SKIP] Kelly stake too small (${kelly_stake:.2f}) — no edge found.")
        return

    outcome = "yes" if decision["action"] == "trade_yes" else "no"
    fm = format_market(market_like)
    token_id = fm["yes_token"] if decision["action"] == "trade_yes" else fm["no_token"]

    conflict, conflict_id = _conflicts_with_existing(fm["question"], outcome, get_open_positions())
    if conflict:
        log(f"[CONFLICT] Skipping — contradicts position #{conflict_id}")
        return

    if any(p["market_id"] == fm["id"] for p in get_all_positions()):
        log(f"[SKIP] Already traded this market — {fm['question'][:60]}")
        return

    pos_id = open_position(
        market_id=fm["id"],
        market_question=fm["question"],
        condition_id=fm["condition_id"],
        outcome=outcome,
        token_id=token_id,
        entry_price=market_price if decision["action"] == "trade_yes" else (1 - market_price),
        size_usdc=round(kelly_stake, 2),
        confidence=decision["confidence"],
        rationale=decision["rationale"],
        mm_type=mm_type,
    )
    log(f"Opened paper position #{pos_id}: {outcome.upper()} on {fm['question'][:60]}")


def run_full_cycle(scan_only: bool = False):
    """Full agent run: scan → analyze → trade."""
    log("=" * 60)
    log(f"AGENT CYCLE START")
    log("=" * 60)

    cfg = load_config()
    print(f"\nCONFIG: capital=${cfg.get('paper_capital', 10000):,.0f} | "
          f"max/trade=${cfg.get('max_capital_per_trade', 300):,.0f} | "
          f"kelly_frac={cfg.get('kelly_fraction', 0.25)} | "
          f"max_open={cfg.get('max_open_positions', 5)}")

    summary = get_summary()
    print(f"PORTFOLIO: {summary['total_positions']} total | "
          f"{summary['open_positions']} open | "
          f"realized P&L: ${summary['realized_pnl']:+,.2f}")

    check_exit_opportunities()

    if scan_only:
        run_scan_only(verbose=True)
        log("Scan-only mode — no trades executed.")
        return

    crypto_candidates = []
    try:
        print("[brain] trying daily crypto scan...")
        flat = _combine_flat_markets(get_daily_crypto_markets(), get_crypto_daily_markets(), get_crypto_updown_markets())
        crypto_candidates = filter_candidate_markets(_wrap_as_crypto_events(flat), min_volume=50_000)
        log(f"Crypto scan: {len(crypto_candidates)} candidates from {len(flat)} raw markets")
    except Exception as e:
        log(f"Crypto scan failed ({e}), continuing with active events only")

    event_candidates = get_all_candidates()
    candidates = _merge_candidates(crypto_candidates, event_candidates)
    log(f"Combined: {len(candidates)} total candidates (crypto + football)")

    if not candidates:
        log("No candidate markets found.")
        return

    _PRIORITY_KEYWORDS = {"up or down", "bitcoin", "price of"}

    def _priority(candidate: tuple) -> int:
        q = candidate[0].get("question", "").lower()
        return 0 if any(kw in q for kw in _PRIORITY_KEYWORDS) else 1

    candidates = sorted(candidates, key=_priority)

    cfg = load_config()
    max_to_analyze = cfg.get("scan_limit", 20)
    to_analyze = candidates[:max_to_analyze]

    log(f"Analyzing top {len(to_analyze)} markets resolving within 24h, sorted by volume...")

    for m, mm_type in to_analyze:
        cfg = load_config()
        open_pos = get_open_positions()
        if len(open_pos) >= cfg.get("max_open_positions", 5):
            log("Max open positions reached — stopping analysis.")
            break
        run_single_trade(m, mm_type)

    log(f"\nCYCLE COMPLETE | Summary: {get_summary()}")


if __name__ == "__main__":
    import json, yaml
    scan_only = "--scan-only" in sys.argv
    run_full_cycle(scan_only=scan_only)
