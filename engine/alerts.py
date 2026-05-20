"""
Telegram alerting for the Polymarket agent.
Sends notifications on: trade open/close, P&L alerts, errors, daily summary.
"""
import asyncio
import json
import os
import logging
import httpx
from datetime import datetime
from pathlib import Path

AGENT_LOG = Path(__file__).parent.parent / "data" / "agent_log.jsonl"
TELEGRAM_CONFIG = Path(__file__).parent.parent / "config.yaml"

logger = logging.getLogger(__name__)


def _get_config():
    import yaml
    if TELEGRAM_CONFIG.exists():
        with open(TELEGRAM_CONFIG) as f:
            return yaml.safe_load(f) or {}
    return {}


def _telegram_enabled() -> bool:
    cfg = _get_config()
    return bool(cfg.get("telegram", {}).get("bot_token") and cfg.get("telegram", {}).get("chat_id"))


async def _send(message: str) -> bool:
    cfg = _get_config()
    tg = cfg.get("telegram", {})
    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"})
            r.raise_for_status()
            logger.info(f"Telegram sent: {message[:80]}")
            return True
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False


async def alert_trade_opened(pos_id: int, question: str, outcome: str,
                              entry_price: float, size: float, rationale: str):
    msg = (
        f"<b>📈 TRADE OPENED</b>\n"
        f"<b>#{pos_id}</b>  {outcome.upper()} @ {entry_price*100:.1f}%\n"
        f"<b>Q:</b> {question[:70]}\n"
        f"<b>Size:</b> ${size:.2f}\n"
        f"<b>Reason:</b> {rationale[:90]}"
    )
    return await _send(msg)


async def alert_trade_closed(pos_id: int, question: str, pnl: float,
                              exit_price: float | None = None):
    emoji = "✅" if pnl >= 0 else "❌"
    price_str = f" @ {exit_price*100:.1f}%" if exit_price else ""
    msg = (
        f"{emoji} <b>TRADE CLOSED</b> #{pos_id}\n"
        f"Exit{price_str}\n"
        f"<b>P&amp;L:</b> ${pnl:+,.2f}\n"
        f"Q: {question[:60]}"
    )
    return await _send(msg)


async def alert_settled(pos_id: int, question: str, won: bool, pnl: float):
    emoji = "🏆 WINNER" if won else "💀 LOST"
    msg = (
        f"{emoji} <b>POSITION SETTLED</b> #{pos_id}\n"
        f"{'✅' if won else '❌'}  <b>P&amp;L: ${pnl:+,.2f}</b>\n"
        f"Q: {question[:60]}"
    )
    return await _send(msg)


async def alert_error(error: str, context: str = ""):
    msg = f"<b>🚨 AGENT ERROR</b>\n{error}\n{context[:70]}"
    return await _send(msg)


async def alert_daily_summary(portfolio: dict, cycle_time: str):
    total = portfolio.get("total_positions", 0)
    open_ = portfolio.get("open_positions", 0)
    pnl = portfolio.get("realized_pnl", 0)
    wins = portfolio.get("wins", 0)
    pct = portfolio.get("win_rate", 0)

    msg = (
        f"<b>📊 DAILY SUMMARY</b>\n"
        f"<b>Cycle:</b> {cycle_time}\n"
        f"<b>Open:</b> {open_} / {total} total\n"
        f"<b>Realized P&amp;L:</b> ${pnl:+,.2f}\n"
        f"<b>Wins:</b> {wins} ({pct:.0f}%)"
    )
    return await _send(msg)
