"""
Tracks price snapshots for scanned markets and surfaces big movers.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_signals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT    NOT NULL,
            question  TEXT,
            price_yes REAL,
            price_no  REAL,
            timestamp TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()


_init_db()


def _parse_prices(m: dict) -> tuple[float, float]:
    try:
        raw = m.get("outcomePrices", "[]")
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = float(prices[0]) if prices else 0.5
        no  = float(prices[1]) if len(prices) > 1 else 1.0 - yes
        return yes, no
    except Exception:
        return 0.5, 0.5


def save_snapshot(markets: list[dict]):
    """Save current YES/NO prices for a list of raw market dicts."""
    ts = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    for m in markets:
        mid = m.get("id") or m.get("condition_id") or ""
        if not mid:
            continue
        yes, no = _parse_prices(m)
        conn.execute(
            "INSERT INTO market_signals (market_id, question, price_yes, price_no, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, m.get("question", ""), yes, no, ts),
        )
    conn.commit()
    conn.close()


def get_price_change(market_id: str) -> float | None:
    """Return absolute change in YES price between the two most recent snapshots."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT price_yes FROM market_signals WHERE market_id=? ORDER BY timestamp DESC LIMIT 2",
        (market_id,),
    ).fetchall()
    conn.close()
    if len(rows) < 2:
        return None
    return rows[0][0] - rows[1][0]


def get_big_movers(threshold: float = 0.10) -> list[dict]:
    """Return markets where YES price moved by ≥ threshold since the previous snapshot."""
    conn = sqlite3.connect(DB_PATH)
    market_ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT market_id FROM market_signals"
    ).fetchall()]

    movers = []
    for mid in market_ids:
        rows = conn.execute(
            "SELECT price_yes, question FROM market_signals "
            "WHERE market_id=? ORDER BY timestamp DESC LIMIT 2",
            (mid,),
        ).fetchall()
        if len(rows) < 2:
            continue
        new_price, question = rows[0]
        old_price = rows[1][0]
        change = new_price - old_price
        if abs(change) >= threshold:
            movers.append({
                "market_id": mid,
                "question":  question,
                "old_price": old_price,
                "new_price": new_price,
                "change":    change,
                "direction": "up" if change > 0 else "down",
            })
    conn.close()
    return movers


def get_signal_summary() -> str:
    """Return a human-readable summary of all big movers."""
    movers = get_big_movers()
    if not movers:
        return ""
    lines = []
    for m in movers:
        arrow = "↑" if m["direction"] == "up" else "↓"
        sign  = "+" if m["change"] > 0 else ""
        lines.append(
            f"SIGNAL: {m['question'][:60]} "
            f"{m['old_price']*100:.0f}% → {m['new_price']*100:.0f}% "
            f"({sign}{m['change']*100:.0f}%) {arrow}"
        )
    return "\n".join(lines)
