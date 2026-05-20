<script lang="ts">
  import { onMount } from 'svelte';
  import api from '$lib/api';

  const TABS = ['overview','positions','scan','trade','crypto','live','settings'] as const;
  type Tab = typeof TABS[number];
  let activeTab: Tab = 'overview';
  $: _ = activeTab;

  const DEFAULTS = {
    paper_capital: 10000, max_capital_per_trade: 300,
    kelly_fraction: 0.25, max_open_positions: 5,
    scan_limit: 20, min_volume: 50_000,
    mode: 'paper', chain: 'polygon',
    wallet_address: '', wallet_private_key: '',
    llm_model: 'gpt-5o', openai_api_key: '',
    api_port: 8011, api_host: '0.0.0.0',
    telegram: { bot_token: '', chat_id: '', log_level: 'INFO' },
    rpc_url: ''
  };


  // ── Data ──────────────────────────────────────────────────────
  let portfolio: any = {};
  let openPos: any[] = [];
  let allPos: any[] = [];
  let scanResults: any[] = [];
  let logLines: string[] = [];
  let settleLog: string[] = [];
  let cryptoMarkets: any[] = [];
  let sportsMarkets: any[] = [];
  let tgConnected = false;
  let scanning = false;
  let analyzing = false;
  let config: Record<string,any> = DEFAULTS;

  // ── Trade form ────────────────────────────────────────────────
  let tradeSlug = '';
  let tradeOutcome: 'yes'|'no' = 'yes';
  let tradeSize = 100;
  let tradeResult: any = null;

  // ── Filter ────────────────────────────────────────────────────
  let filterQuery = '';
  $: filtered = filterQuery
    ? scanResults.filter(m => (m.question||'').toLowerCase().includes(filterQuery.toLowerCase()))
    : scanResults;

  $: pnlColor = (n: number|null|undefined) =>
    typeof n === 'number' ? (n >= 0 ? '#00e676' : '#ff5252') : '#888';
  $: fmtPct = (v: number|null|undefined) =>
    v == null ? '—' : `${(v*100).toFixed(1)}%`;

  async function refresh() {
    try {
      const [
        p, op, ap, sl, lsl, cfg, tg,
        cr, sr
      ] = await Promise.all([
        api.get('/portfolio').then(r=>r.data),
        api.get('/positions/open').then(r=>r.data.positions),
        api.get('/positions?limit=200').then(r=>r.data.positions),
        api.get('/log/settlement?n=50').then(r=>r.data.lines),
        api.get('/log/agent?n=50').then(r=>r.data.lines),
        api.get('/config').then(r=>r.data),
        api.get('/integrations/telegram').then(r=>r.data),
        api.get('/stats/crypto').then(r=>r.data.markets),
        api.get('/stats/sports').then(r=>r.data.markets),
      ]);
      portfolio = p || {};
      openPos  = op || [];
      allPos   = ap || [];
      settleLog= sl || [];
      logLines = lsl || [];
      config   = cfg || {};
      tgConnected = tg?.configured ?? false;
      cryptoMarkets= cr || [];
      sportsMarkets= sr || [];
    } catch(e) { console.error(e); }
  }

  async function doScan() {
    scanning = true;
    try {
      const r = await api.get(`/scan?limit=50`);
      scanResults = r.data.candidates || [];
      await refresh();
    } catch(e) { console.error(e); }
    scanning = false;
  }

  async function doAnalyze() {
    if (!tradeSlug.trim()) return;
    analyzing = true;
    tradeResult = null;
    try {
      const r = await api.post('/trade/analyze', { market_data: { slug: tradeSlug } }, { params: { mm_type: 'crypto' } });
      tradeResult = r.data;
    } catch(e) { tradeResult = { error: String(e) }; }
    analyzing = false;
  }

  async function settleNow() {
    try {
      await api.post('/settle');
      refresh();
    } catch(e) { alert('Settlement error: ' + e.message); }
  }

  async function saveSettings() {
    try {
      await api.post('/config', config);
      refresh();
      alert('Saved');
    } catch(e) { alert(e.message); }
  }

  onMount(() => {
    doScan();    // initial market scan
    refresh();   // poll everything
    const t = setInterval(refresh, 30_000);   // 30s refresh
    return () => clearInterval(t);
  });
</script>

<!-- ════════════════════════════════════════════════════════════
     POLYMARKET AGENT — FULL DASHBOARD
════════════════════════════════════════════════════════════ -->
<div class="app">

  <!-- Header -->
  <header class="hdr">
    <div class="hdr-left">
      <span class="logo">◈</span>
      <span class="title">
        <b>Polymarket Agent</b>
        <span class="badge">v2.0</span>
        <span class="badge" class:live={portfolio.mode==='live'}>{portfolio.mode==='live' ? '●LIVE' : '◌PAPER'}</span>
        <span class="badge" class:ok={tgConnected}>{tgConnected ? '●TG' : '○TG'}</span>
      </span>
    </div>
    <div class="hdr-right">
      <button class="btn-sm" on:click={refresh}>↻ Refresh</button>
    </div>
  </header>

  <!-- Main tabs -->
  <nav class="tabs">
    {#each TABS as t}
      <button
        class="tab" class:active={activeTab===t}
        on:click={()=>activeTab=t}>
        {t.toUpperCase()}
      </button>
    {/each}
  </nav>

  <!-- ════════════════════════════════════════
       OVERVIEW
  ════════════════════════════════════════ -->
  {#if activeTab === 'overview'}
  <div class="view">

    <div class="metrics">
      <div class="mc">
        <div class="ml">Realized P&L</div>
        <div class="mv" style="color:{pnlColor(portfolio.realized_pnl)}">
          ${portfolio.realized_pnl?.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) ?? '0.00'}
        </div>
      </div>
      <div class="mc">
        <div class="ml">Unrealized P&L</div>
        <div class="mv" style="color:{pnlColor(portfolio.unrealized_pnl)}">
          ${portfolio.unrealized_pnl?.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) ?? '0.00'}
        </div>
      </div>
      <div class="mc">
        <div class="ml">Open Positions</div>
        <div class="mv">{portfolio.open_positions ?? 0} / {portfolio.total_positions ?? 0}</div>
      </div>
      <div class="mc">
        <div class="ml">Win Rate</div>
        <div class="mv">{portfolio.win_rate?.toFixed(1) ?? '—'}%</div>
      </div>
      <div class="mc">
        <div class="ml">Total Return</div>
        <div class="mv" style="color:{pnlColor(portfolio.total_pct_return/100)}">
          {portfolio.total_pct_return?.toFixed(2) ?? '—'}%
        </div>
      </div>
      <div class="mc">
        <div class="ml">Scan Candidates</div>
        <div class="mv">{scanResults.length}</div>
      </div>
    </div>

    <div class="grid-2">
      <!-- Open positions -->
      <div class="panel">
        <h3>📊 Open Positions</h3>
        {#if openPos.length===0}
          <p class="muted">No open positions.</p>
        {:else}
          <table>
            <thead><tr><th>#</th><th>Side</th><th>Entry%</th><th>Size$</th><th>Conf</th><th>Question</th></tr></thead>
            <tbody>
            {#each openPos as p}
              <tr>
                <td>#{p.id}</td>
                <td><span class="tag" class:green={p.outcome==='yes'} class:red={p.outcome==='no'}>{p.outcome?.toUpperCase()}</span></td>
                <td>{fmtPct(p.entry_price)}</td>
                <td>${p.size_usdc?.toFixed(2)}</td>
                <td>{(p.confidence*100).toFixed(0)}%</td>
                <td><span title={p.market_question}>{p.market_question?.slice(0,45)}…</span></td>
              </tr>
            {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <!-- Agent log -->
      <div class="panel">
        <h3>📋 Agent Log</h3>
        <pre class="log">{logLines.join('\n')}</pre>
      </div>
    </div>

    <div class="grid-2">
      <!-- Settlement log -->
      <div class="panel">
        <h3>⚖ Settlement Log</h3>
        <pre class="log">{settleLog.join('\n')}</pre>
        <button class="btn" on:click={settleNow}>▶ Settle All</button>
      </div>

      <!-- Leaderboard -->
      <div class="panel">
        <h3>🏆 Win/Loss Leaderboard</h3>
        {#if (config?.mode ?? 'paper') === 'paper'}
          <p class="muted">Paper mode — leaderboard shows simulated trades.</p>
        {/if}
        <table>
          <thead><tr><th>Question</th><th>Type</th><th>P&L$</th></tr></thead>
          <tbody>
          {#each openPos.length > 0 ? allPos.slice(0,20) : [] as p}
            <tr>
              <td><span title={p.market_question}>{p.market_question?.slice(0,50)}…</span></td>
              <td>{p.mm_type?.toUpperCase()??'—'}</td>
              <td style="color:{pnlColor(p.pnl_realized)}">${p.pnl_realized?.toFixed(2) ?? '0.00'}</td>
            </tr>
          {/each}
          {#if allPos.length===0}
            <tr><td colspan="3" class="muted">No positions yet.</td></tr>
          {/if}
          </tbody>
        </table>
      </div>
    </div>

  </div>
  {/if}


  <!-- ════════════════════════════════════════
       POSITIONS
  ════════════════════════════════════════ -->
  {#if activeTab === 'positions'}
  <div class="view">
    <h2>📂 All Positions ({allPos.length})</h2>
    <table class="full">
      <thead>
        <tr><th>#</th><th>Type</th><th>Status</th><th>Outcome</th><th>Entry%</th><th>Size$</th><th>Conf</th><th>P&L$</th><th>Opened</th><th>Question</th></tr>
      </thead>
      <tbody>
      {#each allPos as p}
        <tr>
          <td>#{p.id}</td>
          <td>{p.mm_type?.toUpperCase() ?? '—'}</td>
          <td><span class="tag small">{p.status ?? 'open'}</span></td>
          <td><span class="tag" class:green={p.outcome==='yes'} class:red={p.outcome==='no'}>{p.outcome?.toUpperCase()}</span></td>
          <td>{fmtPct(p.entry_price)}</td>
          <td>${p.size_usdc?.toFixed(2)}</td>
          <td>{(p.confidence*100).toFixed(0)}%</td>
          <td style="color:{pnlColor(p.pnl_realized)}">${p.pnl_realized?.toFixed(2) ?? '0.00'}</td>
          <td class="muted">{(p.opened_at || '').slice(0,10)}</td>
          <td>{p.market_question?.slice(0,60)}</td>
        </tr>
      {/each}
      {#if allPos.length===0}
        <tr><td colspan="10" class="muted">No positions yet.</td></tr>
      {/if}
      </tbody>
    </table>
  </div>
  {/if}


  <!-- ════════════════════════════════════════
       SCAN
  ════════════════════════════════════════ -->
  {#if activeTab === 'scan'}
  <div class="view">
    <div class="row">
      <h2>🔍 Market Scan</h2>
      <div class="row-acts">
        <input class="search" type="text" placeholder="Filter…" bind:value={filterQuery} />
        <button class="btn-primary" disabled={scanning} on:click={doScan}>
          {scanning ? '⏳ Scanning…' : '🔎 Rescan'}
        </button>
      </div>
    </div>

    <div class="sub-tabs">
      <span class="badge">{scanResults.length} total</span>
      <span class="badge green">{scanResults.filter(m=>m.type==='crypto').length} crypto</span>
      <span class="badge">{scanResults.filter(m=>m.type==='sports').length} sports</span>
    </div>

    <table class="full">
      <thead><tr><th>Type</th><th>Question</th><th>Yes%</th><th>No%</th><th>Volume$</th><th>Ends</th></tr></thead>
      <tbody>
      {#each filtered as m}
        <tr>
          <td><span class="tag small" class:green={m.type==='crypto'}>{m.type?.toUpperCase()}</span></td>
          <td>{m.question}</td>
          <td>{fmtPct(m.yes_price)}</td>
          <td>{fmtPct(m.no_price)}</td>
          <td>${m.volume?.toLocaleString()}</td>
          <td class="muted">{(m.end_date||'').slice(0,10)}</td>
        </tr>
      {/each}
      {#if filtered.length===0}
        <tr><td colspan="6" class="muted">No candidates found.</td></tr>
      {/if}
      </tbody>
    </table>
  </div>
  {/if}


  <!-- ════════════════════════════════════════
       TRADE
  ════════════════════════════════════════ -->
  {#if activeTab === 'trade'}
  <div class="view">
    <h2>💹 Trade Analyzer</h2>
    <p class="muted">Run full LLM analysis + Kelly sizing for any market.</p>

    <div class="trade-card">
      <label>Market Slug
        <input type="text" bind:value={tradeSlug} placeholder="will-bitcoin-hit-150k-by-june-2026" />
      </label>
      <label>Outcome
        <select bind:value={tradeOutcome}>
          <option value="yes">YES</option>
          <option value="no">NO</option>
        </select>
      </label>
      <label>Size ($)
        <input type="number" bind:value={tradeSize} min={1} step={1} />
      </label>
      <button class="btn-primary" disabled={analyzing} on:click={doAnalyze}>
        {analyzing ? 'Analyzing…' : '▶ Analyze & Execute'}
      </button>
    </div>

    {#if tradeResult}
      <div class="result-card" class:err={tradeResult.error} class:ok={!!tradeResult.decision}>
        {#if tradeResult.error}
          <p class="err-text">❌ {tradeResult.error}</p>
        {:else if tradeResult.decision}
          <div class="dec-row">
            <b>Action:</b>
            <span class="tag" class:green={tradeResult.decision.action==='BUY'} class:red={tradeResult.decision.action==='PASS'}>
              {tradeResult.decision.action}
            </span>
          </div>
          <p><b>Confidence:</b> {fmtPct(tradeResult.decision.confidence)}</p>
          <p><b>Market Price (Yes):</b> {fmtPct(tradeResult.decision.market_price)}</p>
          <p><b>Edge:</b> {fmtPct(tradeResult.decision.edge)}</p>
          <p><b>Risk Level:</b> {tradeResult.decision.risk}</p>
          <p><b>Suggested Size:</b> ${tradeResult.decision.position_size_usdc?.toFixed(2)}</p>
          <p><b>Rationale:</b> {tradeResult.decision.rationale}</p>
        {/if}
      </div>
    {/if}
  </div>
  {/if}


  <!-- ════════════════════════════════════════
       CRYPTO
  ════════════════════════════════════════ -->
  {#if activeTab === 'crypto'}
  <div class="view">
    <div class="row">
      <h2>₿ Crypto Markets</h2>
      <button class="btn-sm" on:click={refresh}>↻ Refresh</button>
    </div>
    <table class="full">
      <thead><tr><th>#</th><th>Question</th><th>Yes%</th><th>No%</th><th>Volume$</th></tr></thead>
      <tbody>
      {#each cryptoMarkets as m,i}
        <tr>
          <td class="muted">{i+1}</td>
          <td>{m.q}</td>
          <td>{fmtPct(m.yes)}</td>
          <td>{fmtPct(1-(m.yes||0))}</td>
          <td>${m.vol?.toLocaleString()}</td>
        </tr>
      {/each}
      </tbody>
    </table>
  </div>
  {/if}


  <!-- ════════════════════════════════════════
       LIVE / WALLET
  ════════════════════════════════════════ -->
  {#if activeTab === 'live'}
  <div class="view">
    <h2>🔗 Live Mode & Wallet</h2>

    <div class="info-card" class:live={portfolio.mode==='live'} class:paper={portfolio.mode!=='live'}>
      {#if portfolio.mode === 'live'}
        <p class="live-tag">● LIVE MODE</p>
        <div class="kv">
          <span>Chain</span><b>{config.chain?.toUpperCase() ?? 'polygon'}</b>
        </div>
        <div class="kv">
          <span>Address</span><code>{config.wallet_address ?? 'not set'}</code>
        </div>
        <div class="kv">
          <span>USDC Balance</span>
          <b>${(portfolio.wallet_balance ?? 0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</b>
        </div>
      {:else}
        <p class="muted">📝 Paper Mode — no real funds at risk.</p>
        <p>To enable live trading, set these in <code>config.yaml</code>:</p>
        <pre class="code-block">
wallet_private_key: "0xYOUR_KEY"
wallet_address: "0xYOUR_ADDRESS"
mode: "live"
chain: "polygon"</pre>
        <p>Then restart <code>api_server.py</code>.</p>
      {/if}
    </div>

    <div class="panel">
      <h3>Live Position Monitor</h3>
      <table class="full">
        <thead><tr><th>#</th><th>Question</th><th>Outcome</th><th>Entry%</th><th>Size$</th><th>Value$</th><th>P&L$</th></tr></thead>
        <tbody>
        {#each openPos as p}
          <tr>
            <td>#{p.id}</td>
            <td>{p.market_question?.slice(0,55)}</td>
            <td><span class="tag small" class:green={p.outcome==='yes'}>{p.outcome?.toUpperCase()}</span></td>
            <td>{fmtPct(p.entry_price)}</td>
            <td>${p.size_usdc?.toFixed(2)}</td>
            <td class="muted">—</td>
            <td class="muted">—</td>
          </tr>
        {/each}
        {#if openPos.length===0}
          <tr><td colspan="7" class="muted">No open positions.</td></tr>
        {/if}
        </tbody>
      </table>
      <p class="muted">Real-time market value feed coming after live executor integration.</p>
    </div>
  </div>
  {/if}


  <!-- ════════════════════════════════════════
       SETTINGS
  ════════════════════════════════════════ -->
  {#if activeTab === 'settings'}
  <div class="view">
    <div class="row">
      <h2>⚙ Settings</h2>
      <button class="btn-primary" on:click={saveSettings}>💾 Save All</button>
    </div>

    <div class="settings-grid">
      <!-- Agent params -->
      <div class="panel">
        <h3>Agent Parameters</h3>
        <div class="field">
          <label>Paper Capital ($)</label>
          <input type="number" bind:value={config.paper_capital} />
        </div>
        <div class="field">
          <label>Max Per Trade ($)</label>
          <input type="number" bind:value={config.max_capital_per_trade} />
        </div>
        <div class="field">
          <label for="f_kelly_frac">Kelly Fraction</label>
          <input type="number" bind:value={config.kelly_fraction} min={0} max={1} step={0.05} />
        </div>
        <div class="field">
          <label for="f_max_open">Max Open Positions</label>
          <input type="number" bind:value={config.max_open_positions} />
        </div>
        <div class="field">
          <label for="f_scan_limit">Scan Limit</label>
          <input type="number" bind:value={config.scan_limit} min={5} max={200} />
        </div>
        <div class="field">
          <label for="f_mode">Mode</label>
          <select bind:value={config.mode} id="f_mode">
            <option value="paper">PAPER</option>
            <option value="live">LIVE</option>
          </select>
        </div>
        <div class="field">
          <label>Min Volume ($)</label>
          <input type="number" bind:value={config.min_volume} />
        </div>
      </div>

      <!-- Wallet / chain -->
      <div class="panel">
        <h3>Wallet & Chain</h3>
        <div class="field">
          <label for="f_chain">Chain</label>
          <select bind:value={config.chain} id="f_chain">
            <option>polygon</option>
          </select>
        </div>
        <div class="field">
          <label for="f_wallet_addr">Wallet Address</label>
          <input type="text" bind:value={config.wallet_address} placeholder="0x…" />
        </div>
        <div class="field warn">
          <label for="f_wallet_pk">Private Key</label>
          <input type="password" bind:value={config.wallet_private_key} placeholder="0x…" />
        </div>
        <p class="muted">🔒 Private key never leaves this machine.</p>
      </div>

      <!-- Telegram -->
      <div class="panel">
        <h3>Telegram Alerts</h3>
        <p class="muted">Create a bot: <code>@BotFather /newbot</code></p>
        <p class="muted">Get your chat ID: <code>@getidsbot</code></p>
        <div class="field">
          <label for="f_tg_bot">Bot Token</label>
          <input type="text" value={config.telegram?.bot_token || ""} on:input={(e)=>((config.telegram ??= {}).bot_token = e.target.value)} placeholder="123456789:ABC…" />
        </div>
        <div class="field">
          <label for="f_tg_chat">Chat ID</label>
          <input type="text" value={config.telegram?.chat_id || ""} on:input={(e)=>((config.telegram ??= {}).chat_id = e.target.value)} placeholder="-100…" />
        </div>
        <div class="field">
          <label for="f_tg_level">Log Level</label>
          <select value={config.telegram?.log_level || "INFO"} on:change={(e)=>((config.telegram ??= {}).log_level = e.target.value)}>
            <option>INFO</option><option>WARNING</option><option>ERROR</option>
          </select>
        </div>
        <p class="muted">Configured: {tgConnected ? '✅ yes' : '❌ no'}</p>
      </div>

      <!-- API / misc -->
      <div class="panel">
        <h3>API Server</h3>
        <div class="field">
          <label for="f_api_port">Port</label>
          <input type="number" bind:value={config.api_port} id="f_api_port">
        </div>
        <div class="field">
   xt" bind:value={config.api_host} />
        </div>
        <div class="field">
          <label for="f_llm_model">LLM Model</label>
          <input type="text" bind:value={config.llm_model} id="f_llm_model">
        </div>
        <div class="field">
         e="password" bind:value={config.openai_api_key} placeholder="sk-…" />
        </div>
        <p class="muted">Last saved: {config._last_saved ?? 'never'}</p>
      </div>
    </div>
  </div>
  {/if}

</div>

<style lang="css">
  :root {
    --bg: #08080e; --surface: #111119; --surface2: #191933;
    --border: #1e1e38; --accent: #6366f1; --accent2: #22d3a8;
    --red: #ef4444; --green: #10b981; --text: #e2e8f0;
    --muted: #4b5563; --radius: 10px; --font: 'SF Mono','Cascadia Code',monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; }
  .app { min-height: 100vh; }

  /* ── Header ── */
  .hdr { display: flex; align-items: center; justify-content: space-between;
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 12px 24px; position: sticky; top: 0; z-index: 100; }
  .hdr-left { display: flex; align-items: center; gap: 10px; }
  .logo { color: var(--accent); font-size: 1.5em; }
  .title b { font-size: 1.05em; }
  .badge { font-size: 0.65em; padding: 2px 8px; border-radius: 99px;
    background: var(--border); color: var(--muted); margin-left: 6px; }
  .badge.live { color: var(--green); }
  .badge.ok   { color: var(--accent2); }

  /* ── Tabs ── */
  .tabs { display: flex; gap: 2px; background: var(--bg);
    border-bottom: 1px solid var(--border); padding: 8px 20px 0; }
  .tab { background: none; border: none; color: var(--muted);
    padding: 8px 14px; cursor: pointer; font-size: 0.72em;
    letter-spacing: .04em; border-bottom: 2px solid transparent;
    font-family: var(--font); text-transform: uppercase; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* ── View ── */
  .view { padding: 24px; }
  .view h2 { margin: 0 0 20px; font-size: 1.2em; }
  .row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .row-acts { display: flex; gap: 10px; align-items: center; }
  .search { background: var(--surface); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 14px; font-size: 0.82em; min-width: 260px; }
  .sub-tabs { display: flex; gap: 8px; margin-bottom: 14px; }

  /* ── Metrics ── */
  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 12px; margin-bottom: 24px; }
  .mc { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; }
  .ml { font-size: 0.68em; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin-bottom: 6px; }
  .mv { font-size: 1.8em; font-weight: 700; }

  /* ── Panels ── */
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 20px; }
  .panel h3 { margin: 0 0 14px; color: var(--accent); font-size: 0.9em; }
  .panel p { margin: 0 0 8px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }

  /* ── Buttons ── */
  .btn { background: var(--surface2); color: var(--text); border: 1px solid var(--border);
    padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 0.78em; font-family: var(--font); }
  .btn:hover { border-color: var(--accent); }
  .btn-sm { background: var(--surface2); color: var(--text); border: 1px solid var(--border);
    padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 0.75em; font-family: var(--font); }
  .btn-primary { background: var(--accent); color: #fff; border: none;
    padding: 10px 22px; border-radius: 8px; cursor: pointer;
    font-size: 0.82em; font-family: var(--font); }
  .btn-primary:hover { opacity: .85; }
  .btn-primary:disabled { opacity: .35; cursor: not-allowed; }

  /* ── Tables ── */
  table { width: 100%; border-collapse: collapse; font-size: 0.78em; }
  table.full { width: 100%; }
  th { text-align: left; color: var(--muted); font-weight: 500;
    border-bottom: 1px solid var(--border); padding: 8px 10px; }
  td { border-bottom: 1px solid var(--border); padding: 7px 10px; }
  .muted { color: var(--muted); }

  /* ── Tags / badge ── */
  .tag { display: inline-block; padding: 2px 9px; border-radius: 99px;
    font-size: 0.68em; font-weight: 700; text-transform: uppercase; }
  .tag.green { background: rgba(16,185,129,.15); color: var(--green); }
  .tag.red   { background: rgba(239,68,68,.15); color: var(--red); }
  .tag.small { padding: 1px 6px; }

  /* ── Trade card ── */
  .trade-card { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
  .trade-card label { display: flex; flex-direction: column; gap: 5px; font-size: 0.75em; color: var(--muted); }
  .trade-card input, .trade-card select {
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 7px; padding: 9px 14px; font-size: 0.85em;
    font-family: var(--font); min-width: 200px; }
  .result-card { background: var(--bg); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px; margin-top: 14px; font-size: 0.85em; line-height: 1.7; }
  .result-card.err { border-color: rgba(239,68,68,.4); }
  .result-card.ok { border-color: rgba(16,185,129,.3); }
  .err-text { color: var(--red); }
  .dec-row { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; }

  /* ── Log ── */
  .log { background: var(--bg); border-radius: 8px; padding: 12px;
    overflow-y: auto; max-height: 350px; font-size: 0.68em;
    line-height: 1.5; white-space: pre-wrap; word-break: break-all; }

  /* ── Misc ── */
  .info-card { background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px 24px; margin-bottom: 20px; }
  .info-card.live { border-color: rgba(16,185,129,.3); }
  .info-card.paper { border-color: var(--border); }
  .live-tag { color: var(--green); font-size: 1.2em; margin-bottom: 10px; }
  .code-block { background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px; font-size: 0.78em;
    color: var(--accent2); white-space: pre-wrap; margin: 10px 0; }
  .kv { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; font-size: 0.82em; }
  code { color: var(--accent2); font-size: 0.82em; }
  .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
  .field label { font-size: 0.72em; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .field input, .field select {
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 7px; padding: 9px 12px; font-size: 0.82em; font-family: var(--font); }
  .field.warn input { border-color: rgba(239,68,68,.4); }
</style>