# stock-monitor — Build Record

Status key: ✅ done · 🔨 to build · ⏸ blocked on you. Nothing in 🔨 gets built
until you approve this record.

## The strategy this serves (so every output has a job)
- Target ~$2k/mo — a *target the book averages toward*, not a guaranteed monthly draw.
- 1M–3M horizon, fundamental / catalyst-driven. Edge = variant perception on catalysts.
- **MA discipline (the "safer bet"):** only long *above a rising 200DMA*; enter on
  *50DMA pullbacks*; exit on a *200DMA break* (pre-committed invalidation).
- Anti-patsy: trade off *primary sources* (filings, official data), watch *positioning*
  (borrow/short/COT), stay *trend-aligned* with institutions.
- Stay deployed in real 1M/3M setups; cash only as a residual when nothing clears the bar.

## Outputs — what the system produces (5 streams)
1. **Desktop website** (primary) — per-ticker YTD charts, categorised. Your main surface.
2. **Telegram alerts** — strategy-relevant signals pushed to phone.
3. **data.json** — internal machine payload feeding the site.
4. **Positioning + primary-source feeds** (planned) — borrow, scanners, filings, COT.
5. **Morning/evening briefs** (already running, separate & complementary).

---

## A. Desktop website — customise to strategy
- A1 🔨 **Default chart view = YTD** (Jan 1 → today), not full 2-yr history.
- A2 🔨 **Emphasise the 200DMA + 50DMA** (your trend filter + entry line) — thicker/labelled;
  de-emphasise 20/100.
- A3 🔨 **Trend badge per card:** "UP — above rising 200DMA" (green) / "DOWN" (red) /
  "FLAT". Your core go/no-go filter, at a glance.
- A4 🔨 **Entry-setup highlight:** flag a name when price is *near the 50DMA AND above a
  rising 200DMA* (your buy trigger).
- A5 ✅ Today's price + 1D% + YTD% shown. (Price = last close, refreshed up to 4×/day.
  See open question Q3 re: live intraday.)
- A6 ✅ Support/resistance level lines (3m dashed, 1m dotted).
- A7 ✅ Categorised tabs — 9 groups.
- A8 🔨 **Strategy view / sort:** within a category, sort by trend status or entry-readiness
  (surface "what's actionable now" vs raw list).
- A9 ✅ Per-ticker notes + export.

## B. Telegram alerts — customise to strategy
- B1 🔨 **Entry-setup alert** — price pulls back to 50DMA while above rising 200DMA.
- B2 🔨 **Invalidation alert** — price breaks below the 200DMA (your stop trigger).
- B3 ✅ Level-touch alert (18h cooldown).
- B4 🔨 **Catalyst alert** — earnings within N days on a watchlist name.
- B5 🔨 **Borrow-spike alert** — after the IBKR borrow module (C1).

## C. Positioning + primary-source feeds (the anti-patsy layer)
- C1 🔨 **IBKR borrow/shortable feed** — free, works today. Squeeze/crowding per name.
- C2 🔨 **IBKR scanners** — market radar beyond the 59 names.
- C3 🔨 **SEC EDGAR** — Form 4 (insider), 13D/G (activist), 8-K per watchlist.
- C4 🔨 **HKEX CCASS + SGXNet** — HK/SG filings + southbound flow.
- C5 🔨 **CFTC COT** — weekly positioning (CTA proxy).
- C6 ⏸ **Options IV / gamma / implied move** — needs OPRA subscription. Deferred.

## D. Infrastructure
- D1 ✅ 4×/day build + localhost server (launchd, loaded).
- D2 ✅ Staleness guard (hides levels past 30h).
- D3 ✅ git version control.
- D4 ⏸ Secondary IBKR username (session stability) — your ~5-min step.

---

## Proposed build order
1. **Website customisation (A1–A4, A8)** — your stated priority.
2. **Strategy alerts (B1, B2, B4)** — the MA-discipline triggers on your phone.
3. **Borrow feed + scanners (C1, C2)** — positioning, free.
4. **Primary-source feeds (C3–C5)** — filings.
5. **Options (C6)** — only if you subscribe OPRA.

## Open questions before I start
- Q1: Approve the build order above, or reprioritise?
- Q2: "Categorise" — keep the 9 sector groups *and add* a strategy view (sort by
  trend/entry-readiness)? Or one or the other?
- Q3: "Price as of today" — last close refreshed 4×/day is what you have now. Do you
  want *live intraday* price (needs a delayed-quote feed)? Adds complexity.
- Q4: Alert volume — with 59 names, entry/invalidation alerts could be chatty. Cap at
  top-N per run + cooldown, like the level alerts?
