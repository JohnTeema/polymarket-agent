"""
Paper trading ledger — SQLite-backed, no real money involved.
Tracks positions, P&L, realized/unrealized, trade history.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT UNIQUE,
            market_question TEXT,
            condition_id TEXT,
            outcome TEXT,          -- 'yes' or 'no'
            token_id TEXT,
            entry_price REAL,
            size_usdc REAL,
            confidence REAL,
            rationale TEXT,
            mm_type TEXT,          -- 'crypto', 'sports'
            status TEXT DEFAULT 'open',  -- 'open', 'resolved_win', 'resolved_lose', 'closed', 'void'
            opened_at TEXT,
            closed_at TEXT,
            settled_at TEXT,
            pnl_realized REAL DEFAULT 0,
            exit_price REAL,
            notes TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            action TEXT,       -- 'open', 'close', 'settle'
            price REAL,
            size_usdc REAL,
            pnl REAL,
            timestamp TEXT,
            notes TEXT,
            FOREIGN KEY (position_id) REFERENCES positions(id)
        )
    """)
    conn.commit()
    conn.close()


def open_position(
    market_id: str,
    market_question: str,
    condition_id: str,
    outcome: str,
    token_id: str,
    entry_price: float,
    size_usdc: float,
    confidence: float,
    rationale: str,
    mm_type: str,
) -> int:
    """Enter a new paper position. Returns position ID."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    now = datetime.utcnow().isoformat()
    try:
        cur = conn.execute("""
            INSERT INTO positions
            (market_id, market_question, condition_id, outcome, token_id,
             entry_price, size_usdc, confidence, rationale, mm_type, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (market_id, market_question, condition_id, outcome, token_id,
              entry_price, size_usdc, confidence, rationale, mm_type, now))
        pos_id = cur.lastrowid
        conn.execute(
            "INSERT INTO trade_log (position_id, action, price, size_usdc, timestamp, notes) "
            "VALUES (?, 'open', ?, ?, ?, ?)",
            (pos_id, entry_price, size_usdc, now, f"Opened {outcome} on {market_id}"),
        )
        conn.commit()
        print(f"  [PAPER TRADE OPENED] pos={pos_id} | {outcome.upper()} @ {entry_price*100:.1f}% | ${size_usdc:.2f} USDC | conf={confidence:.0%}")
        return pos_id
    finally:
        conn.close()


def close_position(position_id: int, exit_price: float, pnl: float, notes: str = ""):
    """Mark a position as closed (realized P&L)."""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE positions SET status='closed', closed_at=?, exit_price=?, pnl_realized=? WHERE id=?",
        (now, exit_price, pnl, position_id),
    )
    conn.execute(
        "INSERT INTO trade_log (position_id, action, price, size_usdc, pnl, timestamp, notes) "
        "VALUES (?, 'close', ?, ?, ?, ?, ?)",
        (position_id, exit_price, 0, pnl, now, notes),
    )
    conn.commit()
    conn.close()
    print(f"  [PAPER TRADE CLOSED] pos={position_id} | exit={exit_price*100:.1f}% | P&L=${pnl:+.2f}")


def settle_position(position_id: int, outcome_won: bool, final_price: float = 1.0):
    """Mark position as resolved by market outcome."""
    conn = sqlite3.connect(DB_PATH)
    pos = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    if not pos:
        conn.close()
        return
    col_names = ["id", "market_id", "market_question", "condition_id", "outcome",
                 "token_id", "entry_price", "size_usdc", "confidence", "rationale",
                 "mm_type", "status", "opened_at", "closed_at", "settled_at",
                 "pnl_realized", "exit_price", "notes"]
    p = dict(zip(col_names, pos))
    pnl = p["size_usdc"] * (final_price - p["entry_price"]) if outcome_won else -p["size_usdc"]
    status = "resolved_win" if outcome_won else "resolved_lose"
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE positions SET status=?, settled_at=?, exit_price=?, pnl_realized=? WHERE id=?",
        (status, now, final_price if outcome_won else 0.0, pnl, position_id),
    )
    print(f"  [SETTLED] pos={position_id} | {'WIN' if outcome_won else 'LOSS'} | P&L=${pnl:+.2f} | {p['market_question'][:60]}")
    conn.commit()
    conn.close()


def get_open_positions() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    _init_db()
    rows = conn.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY opened_at DESC"
    ).fetchall()
    conn.close()
    col_names = ["id", "market_id", "market_question", "condition_id", "outcome",
                 "token_id", "entry_price", "size_usdc", "confidence", "rationale",
                 "mm_type", "status", "opened_at", "closed_at", "settled_at",
                 "pnl_realized", "exit_price", "notes"]
    return [dict(zip(col_names, r)) for r in rows]


def get_all_positions(limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    _init_db()
    rows = conn.execute(
        f"SELECT * FROM positions ORDER BY opened_at DESC LIMIT {limit}"
    ).fetchall()
    conn.close()
    col_names = ["id", "market_id", "market_question", "condition_id", "outcome",
                 "token_id", "entry_price", "size_usdc", "confidence", "rationale",
                 "mm_type", "status", "opened_at", "closed_at", "settled_at",
                 "pnl_realized", "exit_price", "notes"]
    return [dict(zip(col_names, r)) for r in rows]


def get_summary() -> dict:
    conn = sqlite3.connect(DB_PATH)
    _init_db()
    total_positions = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    open_positions = conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
    total_pnl = conn.execute("SELECT COALESCE(SUM(pnl_realized), 0) FROM positions").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM positions WHERE status IN ('resolved_win', 'closed') AND pnl_realized > 0").fetchone()[0]
    realized_pnl = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions WHERE status != 'open'"
    ).fetchone()[0]
    conn.close()
    return {
        "total_positions": total_positions,
        "open_positions": open_positions,
        "total_pnl": round(total_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "wins": wins,
    }
