"""
Agent orchestrator — main loop.
1. Scan markets → 2. Analyze → 3. Decide → 4. Execute (paper) → 5. Await settlement
"""
import sys
import os
import json as _json
from pathlib import Path
from datetime import datetime
from engine.ledger import (
    open_position, close_position, settle_position, get_open_positions, get_summary, get_all_positions
)
from engine.polymarket_client import (
    get_active_events, format_market
)
from engine.strategies import make_trading_decision, load_config, save_config, filter_candidate_markets


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


def get_all_candidates() -> list:
    """Gather and filter crypto + sports candidates from active events."""
    log("Scanning active events...")
    all_events = get_active_events()
    candidates = filter_candidate_markets(all_events, min_volume=50_000)
    log(f"Found {len(candidates)} candidate markets (min vol $50K)")
    return candidates


def run_scan_only(verbose: bool = False):
    """Just scan and return candidates — no trades."""
    candidates = get_all_candidates()
    for m, mm_type in candidates:
        fm = format_market(m)
        print(f"\n  [{mm_type.upper()}] {fm['question']}")
        print(f"    Yes={fm['yes_price']*100:.1f}% No={fm['no_price']*100:.1f}% | vol=${fm['volume']:,.0f}")
    print(f"\nTotal candidates: {len(candidates)}")
    return candidates


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

    if scan_only:
        run_scan_only(verbose=True)
        log("Scan-only mode — no trades executed.")
        return

    candidates = get_all_candidates()
    if not candidates:
        log("No candidate markets found.")
        return

    # Sort by volume, take top candidates
    candidates_sorted = sorted(candidates, key=lambda x: format_market(x[0])["volume"], reverse=True)
    cfg = load_config()
    max_to_analyze = cfg.get("scan_limit", 20)
    to_analyze = candidates_sorted[:max_to_analyze]

    log(f"Analyzing top {min(len(to_analyze), max_to_analyze)} markets at volume priority...")

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
