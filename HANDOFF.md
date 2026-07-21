# stock-monitor — handoff to Mac mini

Transfer this file + the `~/stock-monitor` directory. Open Claude Code on the mini
in `~/stock-monitor` and point it at this doc.

Written 2026-07-21. Author: previous Claude Code session on the MacBook.

---

## 0. One-paragraph context

> **UPDATE 2026-07-21 (mini):** Chels confirmed she is **no longer at Millennium/UBS**.
> The compliance framing below (§6) that treated read-only as a regulatory wall no
> longer applies. Read-only is **retained as a deliberate preference** — this is a
> monitor, not an execution system — but public-disclosure risk is no longer a
> constraint. Hosting decision is now localhost-only by choice, not by rule.

This is a **read-only** stock monitoring site to support discretionary trading
decisions over an intraday-to-2-month horizon. It is not an execution system and
must never become one — see §6. Core function: daily-bar charts with moving
averages and swing-derived support/resistance for a ~35-name tech/China universe,
per-ticker notes, and Telegram alerts when price reaches a key level.

---

## 1. Decisions already locked (do not relitigate)

| Question | Answer |
|---|---|
| Architecture | Static site + scheduled refresh job. No backend, no server. |
| Refresh cadence | **4 runs/day**: 09:30, 16:00, 21:30, 04:00 HKT (HK open, HK close, US open, US close). KR/TW captured on the nearest run. |
| Price source | **IB Gateway on the mini** (chosen). Yahoo is the working bootstrap — see §3. |
| v1 scope | Charts + MAs + support/resistance + per-ticker notes + breach alerts. |
| Execution | **Read-only. Never creates orders or IBKR order instructions.** |
| Edge modules | All four wanted, built after v1: Tier 1 (hard data), Tier 2 (positioning), Tier 3 (catalysts), news triage. See §5. |
| Universe | 35 lines. See `config/universe.json`. |

---

## 2. Verified facts — these were tested, not assumed

**IBKR MCP does not work headless.** Ran `claude -p "list IBKR tools"` → returned
`NO_IBKR_TOOLS`. The `mcp__claude_ai_Interactive_Brokers_IBKR__*` connector is
session-bound to an interactive claude.ai login. **A cron/launchd job cannot use
it.** This is why IB Gateway is required rather than just calling MCP from the job.

**Yahoo daily bars are accurate.** Cross-checked NVDA: Yahoo daily close 203.28
(20 Jul) vs IBKR live snapshot 204.99 (21 Jul pre-market). Consistent — Yahoo is
correct end-of-day, it just isn't live. **For MA and swing-level math off daily
bars there is no accuracy difference.** IBKR's real advantage is derived data:
IV rank, implied earnings move, option chains for gamma, borrow rates.

**Yahoo covers the whole universe** including HK (`.HK`), Korea (`.KS`),
Taiwan (`.TW`) and the indices. All 35 lines fetch cleanly.

---

## 3. What is built and working right now

```
~/stock-monitor/
  config/universe.json     35 instruments + level-math settings
  scripts/sources.py       YahooSource (working) + IbkrSource (written, untested)
  scripts/levels.py        MAs, swing pivots, clustering, proximity flags
  scripts/build.py         orchestrator → site/data.json
  site/data.json           generated, ~1.6 MB
  .venv/                   python 3.12 via uv, requests installed
```

Run it:
```bash
cd ~/stock-monitor
.venv/bin/python scripts/build.py --source yahoo --no-alert
```
Last run: **35 ok, 0 failed.**

**Level methodology**: swing pivots = bar is an extreme within ±5 bars. Pivots
within 1.5% of each other cluster into one level, weighted by touch count.
Computed separately over 1-month (21 bars) and 3-month (63 bars) lookbacks.
Levels below last close → support; above → resistance. Top 3 each.
MAs: 20/50/100/200 SMA.

**Guards already in place** (both exist because they actually bit during the build):
- `min_bars: 210` — rejects an instrument with too little history to form a real
  200DMA. Caught `HSTECH.HK` silently returning 1 bar and being accepted as valid.
- `build.py` refuses to write `data.json` if zero instruments fetched, so a total
  network failure can't blank the site.

---

## 4. NOT built yet — the remaining v1 work

1. **`site/index.html`** — the actual page. Nothing exists yet. Needs: candlestick
   or line chart per instrument, MA overlays, S/R bands drawn as horizontal zones,
   group tabs, YTD/1D columns, per-ticker note editor.
2. **Notes persistence.** Static site ⇒ options are localStorage (device-bound,
   private, nothing committed) or a committed `notes.json` (syncs, but public if
   the repo is). **Recommendation: localStorage + an export button.** See §6.
3. **Staleness guard in the UI.** `data.json` carries `generated_at` and
   `stale_after_hours: 30`. The page must refuse to render levels older than that
   and show a loud banner instead. **This is not optional** — IB Gateway freezes
   silently and a stale page looks identical to a fresh one.
4. **launchd plist** for the 4 daily runs.
5. **IB Gateway swap** — `IbkrSource` in `sources.py` is written but has never
   run. Needs `uv pip install ib_insync` and a live Gateway to test against.

### Known issue to fix
`data.json` is ~1.6 MB, of which `ma_series` is ~30 KB/instrument. The MA series
is fully derivable from `bars` in the browser. **Drop `ma_series` from the JSON
and compute client-side** — should cut the payload roughly in half.

---

## 5. Edge modules — all four approved, build after v1

Ranked by signal-per-effort. Tier 1 is the strongest and is mostly free public data.

**Tier 1 — hard leading data, free, underwatched**
| Signal | Cadence | Why |
|---|---|---|
| Korea 20-day exports (KITA) | Monthly, 21st | Best real-time read on global semi demand |
| TSMC monthly revenue | Monthly, ~10th | Reads across NVDA/AMD/AVGO/the complex |
| Taiwan monthly revenues | Monthly, ~10th | Supply-chain tier |
| Southbound Stock Connect | Daily, HKEX | Mainland bid into the HK names |
| ADR premium | Live | Scaffolded in `build.py` via `adr_pair` (TSM/2330). **FX-unadjusted — trend only, not the absolute level.** SK Hynix hit a 51% ADR premium in July 2026. |

**Tier 2 — positioning, IBKR-differentiated (needs Gateway)**
IV rank/percentile · implied earnings move from straddles · dealer gamma strikes
(merge into the S/R overlay, don't show separately) · borrow rate + shortable
shares · short interest/days-to-cover · CFTC COT for index futures.

**Tier 3 — mechanical flow**
Earnings calendar with BMO/AMC + implied move · S&P/MSCI/FTSE rebalance dates ·
lockup expiries · FOMC/CPI/NFP/China data.

**News triage** — goal is to *cut* inbound, not add. Dedupe across sources, score
by whether an item touches a watched name or a level currently in play, suppress
the rest. A working Telegram channel scraper already exists at
`~/telegram-reader/scrape_channel.py` (public channels via web preview, verbatim
text + real UTC timestamps, paginates back N days). Channels in use: `@tradehaven`
(~350 posts/wk, SG/macro/markets), `@Fin_Watch` (~64/wk, headlines), `@infinityhedge`
(~5/wk, low volume — do not treat its front page as recent, posts span a month).

**Explicitly dropped, with reasons** — do not rebuild these without a new decision:
- *Retail sentiment* — no clean metric exists; proxies are directional at best.
- *Politician trades* — STOCK Act disclosures carry a 45-day lag. Structurally
  useless at an intraday-to-2-month horizon.
- *Copy trading* — no execution path exists in the tooling, and §6 rules it out.
- *CTA positioning* — proprietary sell-side models. CFTC COT is the honest free
  proxy and it is futures-only.
- *moomoo news* — no public API. Only route is browser automation against a
  logged-in session; fragile and slow.

---

## 6. Constraints that must not be violated

**Read-only.** This system never creates orders, never creates IBKR order
instructions, never auto-executes. ~~Chels works at a hedge fund seconded to a bank;
personal-account dealing rules apply~~ — **superseded 2026-07-21: she is no longer at
Millennium/UBS.** Read-only is now kept as a deliberate design choice (monitor, not
execution engine), enforced belt-and-braces by the IB Gateway read-only API setting.
If a future request pushes toward execution, stop and confirm intent explicitly first.

**Hosting is undecided and matters.** GitHub Pages URLs are public. A public page
listing a Millennium analyst's watchlist and trade notes is a real disclosure
risk. Options: public Pages (not recommended), private repo + GitHub Pro for
private Pages, or **localhost-only on the mini** (recommended default — the mini
is always on, view at `file://` or a local static server, notes never leave the
machine). The build code is identical either way; only the deploy target differs.
**Confirm with Chels before any `git push` to a public remote.**

---

## 7. Setup steps on the mini

### 7.1 Environment
```bash
cd ~/stock-monitor
uv venv --python 3.12          # brew python is broken on this user's machines — always uv
uv pip install requests --python ~/stock-monitor/.venv/bin/python
.venv/bin/python scripts/build.py --source yahoo --no-alert   # should print 35 ok
```

### 7.2 IB Gateway (the hard part — needs Chels present)
1. Download **IB Gateway** (not TWS) from IBKR. Install.
2. Log in once manually. **2FA pushes to IBKR Mobile — this cannot be automated.**
3. Configure: API → Settings → enable ActiveX/Socket clients, port **4001**
   (live) or 4002 (paper), trusted IP `127.0.0.1`, read-only API **enabled**
   (belt-and-braces against §6).
4. Install **IBC** (github.com/IbcAlpha/IBC) for auto-login + auto-restart.
5. `uv pip install ib_insync --python ~/stock-monitor/.venv/bin/python`
6. Test: `.venv/bin/python scripts/build.py --source ibkr --no-alert`

**Expect friction here.** Known failure modes:
- IBKR forces a daily Gateway restart; with auto-restart you get ~1 week before a
  manual phone re-auth is needed. Not set-and-forget.
- Gateway logged in on the mini may log Chels out of TWS/mobile (session exclusivity).
- **Gateway freezes silently** — no error, just stops returning data. The staleness
  guard (§4.3) is the defence. Build it before trusting the IBKR path.
- HK and KRX **realtime** market data are paid subscriptions. Daily historical bars
  are generally fine without them — verify per-exchange during the test run.

If Gateway proves too painful, Yahoo remains a fully valid fallback for everything
in v1 (see §2). Only the Tier 2 positioning module genuinely requires IBKR.

### 7.3 Telegram alerts
Sender already exists at `~/.claude/skills/telegram-sender/send.sh` and is
referenced by `build.py`. Confirm `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are
present on the mini. Drop `--no-alert` to arm alerts.

### 7.4 launchd — 4 runs/day
Write `~/Library/LaunchAgents/com.chels.stockmonitor.plist` with
`StartCalendarInterval` entries for 09:30, 16:00, 21:30, 04:00 (mini's local time,
assumed HKT).

**US DST caveat**: US open/close shift an hour twice a year (21:30/04:00 HKT in
summer, 22:30/05:00 in winter). Either add both sets of entries and let the extra
run be a harmless no-op, or adjust twice yearly. Decide and write it down.

Load: `launchctl load ~/Library/LaunchAgents/com.chels.stockmonitor.plist`

---

## 8. Open questions to settle on the mini

1. **Hosting** — localhost-only vs private Pages vs public. §6. Blocks deploy.
2. **US DST handling** for launchd. §7.4.
3. **Notes storage** — localStorage vs committed file. Falls out of (1).
4. **Chart library** — must be vendored locally, not CDN, if this ever becomes a
   Pages artifact under CSP. Lightweight-charts or uPlot both work offline.
5. **Alert throttling** — a level can be "touched" on all 4 daily runs and fire 4
   times. Needs dedupe/cooldown state, probably a small JSON of last-fired
   timestamps per (instrument, level).

---

## 9. House style (from the user's global CLAUDE.md)

- Python: **f-strings only**, never `.format()`. JS: `const`/`let`, never `var`.
- Always error handling. Small single-purpose functions. Explicit over implicit.
- **No unrequested print/console.log.** No comments explaining obvious code.
- Never overwrite env vars — append or check existence first.
- Don't refactor code you weren't asked to refactor.
- `uv venv --python 3.12` — brew Python is broken on this machine.
- Cron times specified in HKT; convert to UTC only for GitHub Actions.
- Confirm `pwd` and `git remote -v` before any git operation.
- Research/brief output style: thesis first, material items only, tables over prose.

---

## 10. Session log — 2026-07-21 (on the mini)

### Decisions locked this session (settles §8 + §1)
| Open item | Decision |
|---|---|
| Employment / compliance (§0, §6) | Out of MLP/UBS. Constraints relaxed; read-only kept by preference. |
| Price source (§1) | **IB Gateway now** — LIVE account, port 4001, read-only API, IBC auto-login. |
| Relationship to existing 07:00/21:30 brief bots | **Separate & complementary.** Briefs = narrative digest; this = charts + terse level alerts. US-open run moved to :45 to clear the 21:30 evening-brief send. |
| Hosting (§8.1) | **localhost-only** on the mini, `http://127.0.0.1:8787/`. Notes in localStorage. |
| Notes (§8.3) | localStorage + export button. Done. |
| Chart lib (§8.4) | lightweight-charts v4.2.0, vendored at `site/vendor/`. Done. |
| Alert throttle (§8.5) | Per-(instrument,window,kind,price) cooldown, `alert_cooldown_hours: 18`, state in `data/alert_state.json`. Done. |
| US DST (§7.4) | Both seasonal pairs in the plist; off-season run is a harmless refresh. Done. |

### Built and verified this session
- Baseline: `.venv` created, `build.py --source yahoo` → **35 ok, 0 failed**.
- `build.py`: drops `ma_series` (payload **1673 → 893 KB**), computes cooldown-gated alerts.
- `site/index.html` — full page: candles + client-side MAs (20/50/100/200) + S/R price lines,
  group tabs, 1D/YTD columns, vs-MA badges, near-level flags, localStorage notes + export.
- **Staleness guard (§4.3): live.** Page hides all levels + shows a red banner past `stale_after_hours`.
- Telegram sender created at `~/.claude/skills/telegram-sender/send.sh` (uses `MARKET_TELEGRAM_BOT_TOKEN`
  + `TELEGRAM_CHAT_ID` — same channel as the briefs).
- launchd: `com.chels.stockmonitor.plist` (4x/day build) + `com.chels.stockmonitor-web.plist`
  (localhost static server). **Both linted, NOT loaded** — gated on IB verification + go-ahead.
- `ib_insync 0.9.86` installed. IBC template at `config/ibc-config.ini.template`.

### GATED — do not arm until IB Gateway is up + Chels confirms
1. `launchctl load` both plists.
2. Flip `PRICE_SOURCE` in `com.chels.stockmonitor.plist` from `yahoo` → `ibkr`.
3. Drop `--no-alert` is already handled (runner arms alerts); first live run will Telegram.

### IB Gateway — manual steps for Chels (I cannot do these)
1. Download **IB Gateway** (not TWS), install, log in once; approve 2FA on IBKR Mobile.
2. API settings: enable Socket clients, port **4001**, trusted IP `127.0.0.1`, **read-only API on**.
3. Install IBC; `cp config/ibc-config.ini.template ~/ibc/config.ini`, fill user/pass, `chmod 600`.
4. Test: `.venv/bin/python scripts/build.py --source ibkr --no-alert`.

### KNOWN IBKR bugs to fix empirically at first `--source ibkr` run (untested code path)
- Asia lines (SEHK/KSE/TWSE) have **no `currency`** in `universe.json` → may fail `qualifyContracts`.
  Add `currency` (HKD/KRW/TWD) per member as failures name them.
- Indices use `exchange:"SMART"` + `whatToShow:"TRADES"` → wrong for `IND`. Set real exchanges
  (SPX→CBOE, SOX→NASDAQ, HSI/HSTECH→HKFE) and a non-TRADES `whatToShow`.
- HK/KRX realtime data are paid subs; daily historical bars usually fine without — verify per exchange.
