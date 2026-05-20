---
name: polymarket-autonomous-agent
description: Build and operate an autonomous Polymarket trading agent — paper trade first, then live. Focus on crypto and sports markets. Uses tag-based filtering, LLM analysis, and Kelly criterion position sizing.
version: 1.0.0
---

# Polymarket Autonomous Trading Agent

Complete autonomous prediction-market trading agent for Polymarket.

## Architecture

```
~/polymarket-agent/
├── agent.py                     # CLI entry (scan | run | status | positions)
├── api_server.py                # FastAPI REST API (dashboard backend)
├── config.yaml                  # Settings (capital, LLM key, mode, Telegram)
├── data/
│   ├── paper_trades.db          # SQLite ledger
│   ├── agent_log.jsonl          # Agent run log
│   └── settlement_log.jsonl     # Settlement watcher log
├── engine/
│   ├── polymarket_client.py     # Gamma + CLOB + Data API wrapper
│   ├── ledger.py                # Paper/settled SQLite ledger
│   ├── strategies.py            # Tag filter + LLM decision + Kelly sizing
│   ├── brain.py                 # Main orchestrator loop
│   ├── alerts.py                # Telegram alerting
│   ├── settlement_watcher.py    # Auto-settlement daemon
│   └── live_executor.py         # Wallet signing + real trade execution
└── web/                         # SvelteKit dashboard
    └── src/routes/
            +page.svelte         # Main dashboard
            api/                 # API proxy
```

## Commands

```bash
cd ~/polymarket-agent

# CLI
python3 agent.py scan           # list candidates
python3 agent.py run             # full cycle: scan → analyze → trade
python3 agent.py status          # portfolio P&L
python3 agent.py positions       # open positions

# Background services
python3 api_server.py            # FastAPI server on :8011
python3 engine/settlement_watcher.py   # auto-settle loop
python3 engine/live_executor.py --dry-run   # test real execution path
```

## Config

```yaml
# config.yaml
paper_capital: 10000
max_capital_per_trade: 300
kelly_fraction: 0.25
max_open_positions: 5
scan_limit: 20
mode: paper

# LLM analysis (optional, falls back to heuristic)
openai_api_key: ""           # or set env OPENAI_API_KEY
llm_model: gpt-5             # model string for completions

# Telegram alerts (optional)
telegram:
  bot_token: ""              # @BotFather
  chat_id: ""                 # @getidsbot or /start @bot

# Wallet config (live mode only)
wallet_private_key: ""        # DO NOT COMMIT
wallet_address: ""
chain: polygon                # polygon | base
```

## Tag Filtering

Event tags (from Gamma API) classify into sports/crypto/politics (excluded).
Updated live on first scan — no hardcoding beyond the known tag list.
Add new tag slugs to `SPORTS_TAGS`, `CRYPTO_TAGS`, or `EXCLUDED_TAGS` in `strategies.py`.

## Low-Level Notes

- Strategy returns `{action, confidence, market_price, edge, rationale, risk}`
- Kelly: `f* = (bp - q) / b`, stake = `f* × capital × kelly_fraction`
- Min stake threshold: $5 (prevents dust positions)
- Settlement: checks price ≥0.99 or ≤0.01 → fully resolved

## API Endpoints

```
GET  /health                → uptime + process info
GET  /positions             → open + historical positions
GET  /positions/open        → just open
GET  /portfolio             → summary P&L
GET  /scan                  → run scan, return candidates
GET  /scan/match?q=BTC      → filter scan results by query
GET  /markets/active?tag=sports  → active markets filtered by tag
GET  /logs/agent            → tail agent_log
GET  /logs/settlement       → tail settlement_log
POST /trade/analyze         → analyze single market, get LLM decision
POST /trade/execute         → execute trade (paper or live per mode)
POST /settle                → force-run settlement for all open
GET  /config                → current config (redacted secrets)
```

## Dashboard

```bash
npm create svelte@latest web
# Then npm run dev -- --port 5173
# Connect to api_server.py
```

## Live Mode Migration

1. Set `mode: live` in config.yaml
2. Add `wallet_private_key` and `wallet_address`
3. Set `chain: polygon` (USDC on Polygon) or `base`
4. Run `python3 engine/live_executor.py --testnet` to verify signature flow
5. Flip fully live: `python3 engine/live_executor.py --live`

## Risk Controls

- Max $300 / trade (`max_capital_per_trade`)
- Max 5 open positions simultaneously
- 1/4 Kelly (reduces ruin probability by 4× vs full Kelly)
- Auto-settlement after resolution — no zombie positions
- Min $5 stake threshold
- Mode guard: `mode: paper` ignores all real trade calls
