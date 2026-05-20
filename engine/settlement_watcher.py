"""
Auto-settlement watcher — polls open positions, checks if markets resolved,
scores P&L, settles positions, sends Telegram alerts.
Run this as a background process or cron job.
"""
import sys
import os
import json as _json
import logging
import asyncio
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.ledger import get_open_positions, settle_position
from engine.polymarket_client import get_price, get_market
from engine.alerts import _telegram_enabled, alert_settled, alert_error

LOG_PATH = Path(__file__).parent.parent / "data" / "settlement_log.jsonl"
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def log(msg: str):
    ts = datetime.utcnow().isoformat()
    line = _json.dumps({"ts": ts, "msg": msg})
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(f"[{ts[:19]}] {msg}")


async def check_and_settle(verbose: bool = False):
    """Check all open positions and settle any that have resolved."""
    if not _telegram_enabled():
        log("Telegram alerts disabled — settlement will still record but not notify")
    
    open_pos = get_open_positions()
    if not open_pos:
        if verbose:
            log("No open positions.")
        return

    log(f"Checking {len(open_pos)} open position(s)...")
    settled_count = 0

    for pos in open_pos:
        pid = pos["id"]
        q = pos["market_question"]
        outcome = pos["outcome"]  # "yes" or "no"
        condition_id = pos["condition_id"]
        token_id = pos["token_id"]

        try:
            # Fetch current market price
            price_resp = get_price(token_id, side="buy")
            current_price = float(price_resp.get("price", 0))

            # Determine if resolved: price at 1.00 or 0.00 means fully resolved
            is_resolved = current_price >= 0.99 or current_price <= 0.01

            if is_resolved:
                final_price = 1.0 if outcome == "yes" else (0.0 if outcome == "no" else current_price)
                # For Yes position: if Yes resolved (price=1.0) → win, else lose
                won_by_market = (outcome == "yes" and current_price >= 0.99) or \
                                (outcome == "no" and current_price <= 0.01)

                settle_position(pid, won_by_market, final_price)
                settled_count += 1

                # Send Telegram alert asynchronously
                try:
                    asyncio.create_task(
                        alert_settled(pid, q, won_by_market, pos.get("pnl_realized", 0))
                    )
                except Exception:
                    pass

                log(f"Settled #{pid}: {'WIN' if won_by_market else 'LOSS'} | {q[:60]}")
            else:
                if verbose:
                    log(f"  #{pid} still open | {outcome.upper()} | curr_price={current_price:.3f} | {q[:40]}")

        except Exception as e:
            log(f"ERROR settling #{pid}: {e}")
            try:
                asyncio.create_task(alert_error(str(e), f"Settlement #{pid}"))
            except Exception:
                pass

    log(f"Settlement complete: {settled_count} position(s) settled this run.")
