# Polymarket Agent — Quick Start

```
cd ~/polymarket-agent
```

## 1 · Install Python deps

```bash
pip install fastapi uvicorn httpx pyyaml openai  # or: pip install -r requirements.txt
pip install -e .                                  # (once setup.py exists)
```

## 2 · Configure `config.yaml`

```yaml
openai_api_key: "sk-…"         # Required for LLM reasoning
wallet_address: "0x…"          # Leave empty for paper mode
wallet_private_key: "0x…"      # Leave empty for paper mode
```

## 3 · Start API server (Serves dashboard + REST API)

```bash
python api_server.py
# → http://localhost:8011 (API)
# → http://localhost:8011 (dashboard served when web is built)
```

## 4 · Start Dashboard (dev)

```bash
cd web
npm install
npm run dev
# → http://localhost:5173
```

*The dev server proxies to `http://localhost:8011/api` via Vite proxy (add `vite.config.ts` proxy rule).*

## 5 · CLI

```bash
python agent.py scan   # scan-only (no trades)
python agent.py run    # full scan → analyze → execute
python agent.py status # show summary
python agent.py positions
```

---

## Architecture

```
polymarket-agent/
├── api_server.py           ← FastAPI server (REST + static dev proxy)
├── agent.py                ← CLI entry point
├── config.yaml             ← All config (capital, keys, mode)
├── settings.json           ← Runtime overrides
├── start.sh                ← Launch everything
├── docker-compose.yaml     ← (optional)
├── requirements.txt
├── brain.py                ← Orchestrator
├── engine/
│   ├── polymarket_client.py ← Gamma API client
│   ├── strategies.py        ← LLM decision engine + Kelly sizing
│   ├── ledger.py            ← SQLite paper/live ledger
│   ├── alerts.py            ← Telegram bot
│   ├── settlement_watcher.py← Auto-settlement loop
│   └── live_executor.py     ← Wallet signing + CLOB execution
├── data/
│   └── ledger.db            ← SQLite positions
├── web/                     ← SvelteKit dashboard
│   └── src/lib/api.js       ← Browser fetch wrapper
│   └── src/routes/+page.svelte
└── scripts/
```

---

## CRON / Monitor Setup

Terminal-recommended background processes (no god process needed):

```bash
# In separate terminal windows / TMUX panes:

# 1 — API server
python ~/polymarket-agent/api_server.py

# 2 — Auto-settlement watcher (every 2 min)
watch -n 120 "python ~/polymarket-agent/engine/settlement_watcher.py"

# 3 — Agent loop (every 4 hours)
watch -n 14400 "cd ~/polymarket-agent && python agent.py run"

# Or use a cron job:
0 */4 * * * cd ~/polymarket-agent && python agent.py run >> /tmp/agent_cron.log 2>&1
*/10 * * * * python ~/polymarket-agent/engine/settlement_watcher.py >> /tmp/agent_settle.log 2>&1
```

---

## Live Mode Switch

1. Add `wallet_private_key` and `wallet_address` to `config.yaml`
2. Set `mode: live`
3. Restart `api_server.py`
4. Configure Telegram:

```bash
curl -X POST http://localhost:8011/config \
  -H "Content-Type: application/json" \
  -d '{"telegram":{"bot_token":"YOUR_BOT_TOKEN","chat_id":"YOUR_CHAT_ID","log_level":"INFO"}}'
```

---

## Dashboard Tabs

| Tab | What it shows |
|-----|---------------|
| Overview | P&L summary, open positions, live agent/settlement logs |
| Positions | All trades by opened-at |
| Scan | Candidate markets, filterable by tag type |
| Trade | Run LLM analysis + Kelly sizing on any market slug |
| Crypto | Live crypto market feed (BTC, ETH, SOL…) |
| Live | Wallet status, chain, balance, live position monitor |
| Settings | Config editor — capital, Kelly fraction, mode, keys |

---

## Live Executor — stub notes

`engine/live_executor.py` has:

| Function | Status |
|----------|--------|
| EIP-712 wallet signing | Protocol scaffolding present — walletconnect/eth-account needed for actual sig |
| CLOB REST order | POST scaffold — Gamma REST key required |
| Auto-settle | Wallet settlement stub |
| `main(argparse)` | `--dry-run`, `--live` stubs |

To wire real signing, inspect the code paths and add your chosen wallet popup (e.g., `eth_account` for local key signing, or wallet-connect for Ledger/MM).

---

## Data flow

```
Gamma API → get_active_events()
       ↓
strategies.filter_candidate_markets()   ← tag-based crypto/sports filter
       ↓
brain.run_full_cycle()                  ← LLM reasoning → Kelly position size
       ↓
ledger.open_position()                  ← SQLite paper/live record
       ↓
settlement_watcher — polls resolution → ledger.settle_position()
       ↓
alerts.send_telegram_message()          ← trade open/close/settle notifications
```

---

## Files changed

| File | Status |
|------|--------|
| `api_server.py` | Lens-rewritten with all endpoints |
| `web/src/routes/+page.svelte` | 7-tab full frontend |
| `web/src/lib/api.js` | Browser API proxy |
| `engine/alerts.py` | Telegram bot (long‑polling) |
| `engine/settlement_watcher.py` | Auto-settlement loop |
| `engine/live_executor.py` | Wallet signing + CLOB stub |
| `engine/strategies.py` | Rewritten — tag sets, confidence scoring, Kelly |
| `engine/brain.py` | Telemetry stubs added |
| `config.yaml` | Restructured for clarity |
| `web/package.json` | Dashboard dependencies |
| `web/svelte.config.js` / `tsconfig.json` / `vite.config.js` | Starter configs |
