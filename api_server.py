"""
FastAPI server — powers the dashboard + REST API.

Endpoints
---------
GET  /health                      → uptime + config snapshot
GET  /config                      → agent config (secrets redacted)
GET  /portfolio                   → P&L summary
GET  /positions                   → all positions
GET  /positions/open              → open positions only
GET  /positions/{pid}             → single position detail
GET  /scan                        → run scan, return candidates
GET  /scan/match?q=BTC            → filter scan results
GET  /markets/active?tag=tag      → active markets by tag
GET  /log/agent                   → tail agent_log
GET  /log/settlement              → tail settlement_log
GET  /stats/crypto                → crypto-specific stats
GET  /stats/sports                → sports-specific stats
GET  /stats/leaderboard            → win/loss leaderboard
POST /trade/analyze               → analyze single market
POST /agent/run                   → trigger a scan cycle
POST /settle                      → manual settlement
GET  /                            → serve Svelte build or redirect to /web/dev
"""

import sys
import os
import json as _json
import logging
import time
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import yaml
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import (
    FileResponse, JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
)
from fastapi.middleware.cors import CORSMiddleware
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR

from engine.ledger import (
    get_all_positions, get_open_positions, get_summary, open_position,
    close_position, settle_position,
)
from engine.polymarket_client import get_active_events, _get, format_market, get_market
from engine.strategies import filter_candidate_markets
from engine.brain import run_full_cycle, log as brain_log

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(module)s | %(message)s")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
LOG_PATH = ROOT / "data" / "agent_log.jsonl"
SETTLE_LOG = ROOT / "data" / "settlement_log.jsonl"
WEB_BUILD = ROOT / "web" / ".svelte-kit" / "output"   # SvelteKit adapter output
DASHBOARD_HTML = ROOT / "web" / "dashboard.html"       # vanilla fallback dashboard
WEB_DEV = ROOT / "web"

_started_at = time.time()
app = FastAPI(title="Polymarket Autonomous Agent API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def redacted_config(cfg: dict) -> dict:
    d = dict(cfg)
    for k in ("wallet_private_key", "openai_api_key"):
        if k in d and d[k]:
            d[k] = f"{str(d[k])[:4]}***[REDACTED]"
    tg = d.get("telegram", {})
    if isinstance(tg, dict):
        for k2 in ("bot_token",):
            if k2 in tg and tg[k2]:
                tg[k2] = f"{tg[k2][:4]}***REDACTED"
    return d


def tail_file(path: Path, n: int = 80) -> list[str]:
    if not path.exists():
        return []
    with open(path) as f:
        lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n:]]


# ── Health ───────────────────────────────────────────────────
@app.get("/health")
def health():
    cfg = load_config()
    uptime = time.time() - _started_at
    return {
        "status": "ok",
        "uptime_secs": round(uptime),
        "uptime": f"{uptime//3600}h{(uptime%3600)//60:.0f}m",
        "mode": cfg.get("mode", "paper"),
        "opens": get_open_positions(),
        "config_redacted": redacted_config(cfg),
    }


# ── Config ───────────────────────────────────────────────────
@app.get("/config")
def get_config():
    return redacted_config(load_config())


# ── Portfolio ────────────────────────────────────────────────
@app.get("/portfolio")
def portfolio():
    s = get_summary()
    closed = get_all_positions(10000)
    wins = [p for p in closed if p.get("status") in ("resolved_win", "closed") and p.get("pnl_realized", 0) > 0]
    all_wins = [p for p in closed if p.get("pnl_realized", 0) > 0]
    return {
        **s,
        "win_rate": (len(wins) / max(s["total_positions"] - s["open_positions"], 1)) * 100,
        "total_pct_return": (s["realized_pnl"] / max(s.get("paper_capital", 10000), 1)) * 100,
    }


# ── Positions ────────────────────────────────────────────────
def _serialize_p(p: dict) -> dict:
    return {k: str(v)[:120] if isinstance(v, str) else v for k, v in p.items()}


@app.get("/positions")
def list_positions(limit: int = Query(200, ge=1, le=1000)):
    pos = get_all_positions(limit)
    return {"positions": [_serialize_p(p) for p in pos], "count": len(pos)}


@app.get("/positions/open")
def list_open():
    pos = get_open_positions()
    return {"positions": [_serialize_p(p) for p in pos], "count": len(pos)}


@app.get("/positions/{pid}")
def get_position(pid: int):
    pos = get_all_positions(1000)
    p = next((x for x in pos if x["id"] == pid), None)
    if not p:
        raise HTTPException(404, f"Position #{pid} not found")
    return _serialize_p(p)


# ── Scan ─────────────────────────────────────────────────────
@app.get("/scan")
def scan(limit: int = Query(20, ge=1, le=100)):
    try:
        events = get_active_events(limit=limit)
        candidates = filter_candidate_markets(events, min_volume=load_config().get("min_volume", 50_000))
        out = []
        for m, t in candidates:
            fm = format_market(m)
            out.append({
                "type": t,
                "id": fm["id"],
                "question": fm["question"],
                "slug": fm["slug"],
                "yes_price": fm["yes_price"],
                "no_price": fm["no_price"],
                "volume": fm["volume"],
                "end_date": fm["end_date"],
                "condition_id": fm["condition_id"],
                "yes_token": fm["yes_token"],
                "no_token": fm["no_token"],
            })
        return {"candidates": out, "count": len(out)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/scan/match")
def scan_match(q: str = Query(..., min_length=2), limit: int = 50):
    """Return candidates whose question contains q (case-insensitive)."""
    events = get_active_events(limit=100)
    candidates = filter_candidate_markets(events)
    matches = []
    for m, t in candidates:
        fm = format_market(m)
        if q.lower() in fm["question"].lower():
            matches.append({
                "type": t, "question": fm["question"],
                "yes_price": fm["yes_price"], "volume": fm["volume"],
            })
        if len(matches) >= limit:
            break
    return {"matches": matches, "count": len(matches)}


# ── Market detail ────────────────────────────────────────────
@app.get("/markets/active")
def markets_active(tag: str = Query(None), limit: int = 50):
    events = get_active_events(limit=limit)
    if tag:
        tag_set = {t.lower() for t in tag.split(",")}
        events = [e for e in events if tag_set & {str(tt).lower() for tt in e.get("tags", [])}]
    candidates = filter_candidate_markets(events)
    out = []
    for m, t in candidates:
        fm = format_market(m)
        out.append({"type": t, **{k: v for k, v in fm.items() if v is not None}})
    return {"markets": out, "count": len(out)}


# ── Log tail ─────────────────────────────────────────────────
@app.get("/log/agent")
def log_agent(n: int = Query(80, ge=10, le=500)):
    lines = tail_file(LOG_PATH, n)
    return {"lines": lines, "count": len(lines)}


@app.get("/log/settlement")
def log_settlement(n: int = Query(80, ge=10, le=500)):
    lines = tail_file(SETTLE_LOG, n)
    return {"lines": lines, "count": len(lines)}


# ── Agent control ────────────────────────────────────────────
@app.post("/agent/run")
def agent_run(async_: bool = Query(False), scan_only: bool = False):
    """Trigger a full agent cycle (or scan-only)."""
    try:
        if async_:
            import threading
            t = threading.Thread(
                target=run_full_cycle,
                kwargs={"scan_only": scan_only},
                daemon=True,
            )
            t.start()
            return {"status": "started_async", "pid": t.ident}
        else:
            run_full_cycle(scan_only=scan_only)
            return {"status": "completed"}
    except Exception as e:
        raise HTTPException(500, f"Cycle failed: {traceback.format_exc()}")


# ── Single market analyze ─────────────────────────────────────
@app.post("/trade/analyze")
def analyze(market_data: dict, mm_type: str = "crypto"):
    """Analyze a market (dict with market_id or slug or free-text query) and return decision."""
    try:
        from engine.strategies import make_trading_decision
        slug   = market_data.get("slug",   "")
        q      = market_data.get("query",  slug)   # free-text fallback
        m_raw  = market_data.get("market", {})

        # 1 · explicit market data passed from scan tab
        if m_raw and (m_raw.get("id") or m_raw.get("condition_id")):
            m = m_raw

        # 2 · canonical slug → Gamma API lookup
        elif slug:
            m = get_market(slug)

            # 3 · free-text search fallback
            if m is None and len(q) >= 3:
                events   = get_active_events(limit=200)
                from engine.strategies import filter_candidate_markets, format_market
                candidates = filter_candidate_markets(events)
                ql = q.lower()
                hit = next(
                    (c for c, t in candidates if ql in c.get("question","").lower()),
                    None,
                )
                if hit:
                    m = hit

        else:
            m = None

        if not m:
            return {"decision": None, "note": f"No market found for '{q}'. Check the Scan tab for exact slugs."}

        d = make_trading_decision(m, mm_type)
        if not d:
            return {"decision": None, "note": "max positions reached or no signal"}
        return {"decision": d}
    except Exception as e:
        import traceback as tb
        raise HTTPException(500, f"{e}\n{tb.format_exc()}")


# ── Manual settlement ─────────────────────────────────────────
@app.post("/settle")
def run_settlement():
    try:
        import asyncio
        from engine.settlement_watcher import check_and_settle
        asyncio.run(check_and_settle(verbose=True))
        return {"status": "completed"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Stats ─────────────────────────────────────────────────────
@app.get("/stats/crypto")
def stats_crypto():
    events = get_active_events(200)
    candidates = filter_candidate_markets(events)
    c_crypto = [m for m, t in candidates if t == "crypto"]
    top = sorted(c_crypto, key=lambda m: float(m.get("volume", 0) or 0), reverse=True)[:20]
    return {"markets": [{"q": m["question"][:80], "yes": float(m.get("outcomePrices","[0,0]")[1]), "vol": float(m.get("volume", 0))} for m in top]}


@app.get("/stats/sports")
def stats_sports():
    events = get_active_events(200)
    candidates = filter_candidate_markets(events)
    c_sports = [m for m, t in candidates if t == "sports"]
    top = sorted(c_sports, key=lambda m: float(m.get("volume", 0) or 0), reverse=True)[:20]
    return {"markets": [{"q": m["question"][:80], "yes": float(m.get("outcomePrices","[0,0]")[1]), "vol": float(m.get("volume", 0))} for m in top]}


@app.get("/stats/leaderboard")
def stats_leaderboard():
    pos = get_all_positions(10000)
    closed = [p for p in pos if p.get("status") not in ("open", "void", None)]
    rows = [(p.get("market_question", "")[:60], p.get("mm_type", "?"), p.get("pnl_realized", 0))
            for p in sorted(closed, key=lambda x: x.get("pnl_realized", 0), reverse=True)]
    return {"rows": rows[:30]}


# ── Telegram status ───────────────────────────────────────────
@app.get("/integrations/telegram")
def tg_status():
    cfg = load_config()
    tg = cfg.get("telegram", {})
    has_token = bool(tg.get("bot_token"))
    has_chat   = bool(tg.get("chat_id"))
    return {
        "configured": has_token and has_chat,
        "has_token": has_token,
        "has_chat_id": has_chat,
    }


# ── Serve dashboard assets ────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    if (WEB_BUILD / "index.html").exists():
        return FileResponse(str(WEB_BUILD / "index.html"))
    if DASHBOARD_HTML.exists():
        return FileResponse(str(DASHBOARD_HTML))
    return HTMLResponse(
        content="<html><body><h3>Polymarket Agent API</h3>"
                "<p>Dashboard not built. Run <code>npm run build</code> in <code>web/</code>, "
                "or place a <code>dashboard.html</code> there.</p>"
                "<p>API docs: <a href='/docs'>/docs</a></p></body></html>",
        status_code=200,
    )


if __name__ == "__main__":
    cfg = load_config()
    port = int(cfg.get("api_port", 8011))
    host = cfg.get("api_host", "0.0.0.0")
    logger.info(f"Starting Polymarket Agent API on {host}:{port}")
    uvicorn.run("api_server:app", host=host, port=port, reload=False)
