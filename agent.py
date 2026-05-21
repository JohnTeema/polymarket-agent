#!/usr/bin/env python3
"""
Polymarket Autonomous Trading Agent

Usage:
    python3 agent.py scan              # Scan only — no trades
    python3 agent.py run               # Full cycle: scan → analyze → trade (paper)
    python3 agent.py status            # Show portfolio summary
    python3 agent.py positions         # List open positions
"""

import sys
import json as _json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine.brain import run_full_cycle, run_scan_only, check_exit_opportunities, settle_positions, get_all_positions, get_summary, get_open_positions, log


def show_status():
    s = get_summary()
    print(f"\n{'='*50}")
    print(f"  PORTFOLIO SUMMARY")
    print(f"{'='*50}")
    print(f"  Total positions:      {s['total_positions']}")
    print(f"  Open positions:       {s['open_positions']}")
    print(f"  Total P&L (all):      ${s['total_pnl']:+,.2f}")
    print(f"  Realized P&L:         ${s['realized_pnl']:+,.2f}")
    wins = s['wins']
    total_closed = s['total_positions'] - s['open_positions']
    win_rate = wins / total_closed * 100 if total_closed > 0 else 0
    print(f"  Win rate (closed):    {win_rate:.1f}%")
    print()


def show_positions():
    pos = get_open_positions()
    if not pos:
        print("\nNo open positions.")
        return
    print(f"\n{'='*50}")
    print(f"  OPEN POSITIONS ({len(pos)})")
    print(f"{'='*50}")
    for p in pos:
        print(f"\n  #{p['id']} [{p['mm_type'].upper()}] {p['outcome'].upper()}")
        print(f"  Question: {p['market_question'][:70]}")
        print(f"  Entry: {p['entry_price']*100:.1f}% | Size: ${p['size_usdc']:.2f}")
        print(f"  Confidence: {p['confidence']*100:.0f}%")
        print(f"  Opened: {p['opened_at'][:10]}")
        print(f"  Rationale: {p['rationale'][:80]}")


def print_banner():
    banner = """
╔══════════════════════════════════════════╗
║   POLYMARKET AUTONOMOUS TRADING AGENT    ║
║         Crypto + Sports | Paper First    ║
╚══════════════════════════════════════════╝
"""
    print(banner)


def print_status():
    import yaml
    cfg_path = Path(__file__).parent / "config.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    print(f"\nCONFIG:")
    print(f"  Mode:        {cfg.get('mode', 'paper').upper()}")
    print(f"  Capital:     ${cfg.get('paper_capital', 0):,.0f}")
    print(f"  Max/trade:   ${cfg.get('max_capital_per_trade', 300):,.0f}")
    print(f"  Kelly frac:  {cfg.get('kelly_fraction', 0.25)}")
    print(f"  Max open:    {cfg.get('max_open_positions', 5)}")
    print(f"  Scan limit:  {cfg.get('scan_limit', 20)}")
    has_key = bool(cfg.get("openai_api_key", ""))
    print(f"  LLM key:     {'SET' if has_key else 'NOT SET (heuristic mode)'}")


if __name__ == "__main__":
    print_banner()
    if len(sys.argv) < 2:
        print("Usage: python3 agent.py [scan|run|exit|settle|status|positions]")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "scan":
        print("SCAN MODE — no trades\n")
        run_scan_only()
    elif cmd == "run":
        run_full_cycle(scan_only=False)
        print_status()
    elif cmd == "exit":
        print("EXIT CHECK — scanning open positions for profit targets / stop losses\n")
        check_exit_opportunities()
    elif cmd == "settle":
        print("SETTLE — checking resolved markets and closing positions\n")
        settle_positions()
    elif cmd == "status":
        show_status()
    elif cmd == "positions":
        show_positions()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: agent.py [scan|run|exit|settle|status|positions]")
