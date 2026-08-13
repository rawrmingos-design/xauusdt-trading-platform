# MT5 Phase 3 — Execution Semantics Contract (PROJECT-MT5-002)

> **Status: DRAFT — for Tech Lead review. No `order_send` implementation before approval.**
>
> This document defines the *exact contract* for demo execution before any
> `order_send()` code is written. It answers the eight questions raised by the
> Tech Lead. Once approved, this document is the normative spec for
> `src/xauusdt/execution/mt5/` Phase 3 implementation.

## 0. Scope & boundary

```
OrderIntent
   ↓
Risk validation (risk engine — unchanged)
   ↓
mode guard (MT5_MODE=demo + actual account mode=DEMO)
   ↓
order_check()       ← validation pass, no state change
   ↓
order_send()        ← single submission
   ↓
retcode classification
   ↓
reconcile()         ← position/order/deal truth from MT5
   ↓
MT5 position
```

- **Demo-only.** `MT5_MODE=demo` AND actual account mode `ACCOUNT_TRADE_MODE_DEMO`
  are both required for the write path. Any other combination → `ModeGuardError`,
  write path refuses. There is no live path in this project.
- All timestamps UTC. All monetary/volume values are floats passed to MT5 as-is
  (MT5 API uses floats; domain layer validates precision before this layer).

---

## 1. PLACED vs DONE — submission outcome semantics

### 1.1 The two retcodes

| Retcode | Meaning | Domain mapping |
|---|---|---|
| `TRADE_RETCODE_PLACED` (10008) | **Pending order** placed on the book (limit/stop). Not filled. | `ExecutionResult.ok=True`, `state=SUBMITTED`, order is *open pending* |
| `TRADE_RETCODE_DONE` (10009) | **Request completed** — market order filled, or pending order activated/filled. | `ExecutionResult.ok=True`, `state=FILLED` |

### 1.2 Market orders

A market order (`ORDER_TYPE_BUY/SELL`) that returns `DONE` is **filled** at
execution time. The deal ticket is available via `MT5.order_get()` /
`MT5.deals_get()` reconciliation — we do NOT trust the raw response alone.

A market order must never return `PLACED` for a market order type (MT5 fills
market orders immediately or rejects them). If it ever does, treat as a bug →
`VENUE_ERROR` + manual investigation, do not assume fill.

### 1.3 Pending orders (limit/stop)

- `PLACED` → pending order live on the book. `state=SUBMITTED`, wait for
  activation. No fill yet.
- `DONE` for a pending order → the pending order was **executed/activated**
  (turned into a position or was a closing order that completed). `state=FILLED`.
- `DONE_PARTIAL` → partial fill; the remaining part stays on the book as a
  pending order (MT5 re-quotes the remainder). `state=PARTIALLY_FILLED`.

### 1.4 Deferred outcome — the general rule

`order_send()` only tells us *what the venue accepted*. The **source of truth is
MT5 state** (orders + positions + deals) fetched in `reconcile()`. The contract:

> **A submission is only "successful" when `reconcile()` confirms the expected
> post-state (open pending order / open position / no residual), not when
> `order_send()` returns a success retcode.**

Consequence: `order_send()` returning `PLACED`/`DONE` is necessary but not
sufficient. Every submission is followed by `reconcile()` (see §5).

---

## 2. Timeout with unknown outcome — the "black box" window

### 2.1 The problem

`order_send()` may raise a transport error, time out, or return `TIMEOUT`
(10012) *after* the venue already accepted the order. We cannot distinguish
"never accepted" from "accepted but response lost" from the raw retcode.

### 2.2 The rule — resolve by ticket, never resend blindly

On timeout/unknown outcome:

1. **Do NOT resend the same intent.** Duplicate submission risk (§4) is worse
   than a missed fill.
2. **Enter the `UNKNOWN_OUTCOME` state** for that intent (persisted, see §8).
3. **Reconcile immediately** (same poll cycle): fetch pending orders +
   positions + deals for the last N seconds and look for a ticket matching our
   intent fingerprint (symbol + side + volume + timestamp window + magic).
4. **Resolution:**
   - Found matching order/position/deal → submission actually succeeded.
     Update state to `SUBMITTED`/`FILLED` accordingly. Continue normal flow.
   - Not found after reconciliation → the request was likely rejected before
     acceptance. Mark intent `FAILED_UNKNOWN`, surface for operator review.
     **Never auto-retry**; an operator (human or supervised harness) decides.
5. `UNKNOWN_OUTCOME` intents are never silently dropped; they block
   reconciliation accounting until resolved (or operator overrides).

### 2.3 Retry policy (bounded, only for known-transient)

Only these retcodes auto-retry (max 3 attempts, exponential backoff 1s/2s/4s),
because they are documented as transient and *pre-acceptance*:
`REQUOTE`, `PRICE_CHANGED`, `PRICE_OFF`, `TIMEOUT`, `TOO_MANY_REQUESTS`.
Each retry re-runs `order_check()` first. Anything else → no retry.

---

## 3. Partial fill — DONE_PARTIAL handling

### 3.1 Recognition

- `order_send()` returns `DONE_PARTIAL` (10010), or
- `reconcile()` observes a pending order whose filled volume < requested volume.

### 3.2 Handling

1. Record `filled_volume` vs `requested_volume` on the intent.
2. `state=PARTIALLY_FILLED`, classification `RetcodeClass.PARTIAL`.
3. The remainder stays as a live pending order on the MT5 book (MT5 behaviour).
4. **Decision — no auto-replace.** We do not cancel + re-place the remainder
   automatically. Rationale: price may have moved; an auto-replace duplicates
   risk. We surface the partial and let the strategy layer decide
   (cancel remainder / leave it / widen).
5. Reconciliation tracks the remainder until it fills fully (`FILLED`),
   expires (`EXPIRED`), or is cancelled (`CANCELLED`).

---

## 4. Duplicate submission prevention (idempotency)

### 4.1 Why it matters

A retry of a *successful* submission = two orders. MT5 has **no native
idempotency key**. The client must provide one.

### 4.2 Client-side request fingerprint (magic + comment)

Every `Mt5OrderRequest` carries:

- `magic` — fixed per-strategy magic number (stable, e.g. from config)
- `comment` — **unique monotonic request id** `xauusdt-<run_id>-<seq>`
  generated once per `OrderIntent`, persisted with the intent.

The pair `(magic, comment)` is the idempotency key.

### 4.3 Enforcement points

1. **Before send**: query `MT5.orders_get(comment=...)`. If a pending order
   with the same comment exists → this intent was already placed →
   treat as `SUBMITTED` (idempotent hit), do not send again.
2. **Before send**: query `MT5.deals_get(comment=...)`. If a deal with the same
   comment exists → already executed → treat as `FILLED`.
3. **After UNKNOWN_OUTCOME** (§2.2): the first reconciliation step is the same
   comment lookup — never guess by price alone.
4. **In-process guard**: the execution layer keeps an in-memory
   `set[request_id]` of in-flight submissions; a second submit of the same
   request id while the first is unresolved → `DuplicateSubmissionError`.

### 4.4 Magic/comment conventions

- `magic`: from `Mt5Settings.magic`, default `0` is forbidden (must be set).
- `comment`: `f"xauusdt-{run_id}-{seq:08d}"` — seq is a monotonically
  increasing counter persisted in the execution store (survives restart).

---

## 5. Reconcile — order ticket vs deal ticket vs position ticket

### 5.1 The three ticket universes (do not conflate)

| Universe | MT5 API | Represents | Identified by |
|---|---|---|---|
| **Order ticket** | `orders_get()` | pending/active order (incl. market order request) | `ticket` |
| **Deal ticket** | `deals_get()` | executed fill (buy/sell transaction) | `deal` |
| **Position ticket** | `positions_get()` | open position (netting/hedging) | `position` |

Key facts:

- A **market fill** creates: 1 order ticket (often `0`/void or the request id)
  + 1 deal ticket + 1 position ticket. All different numbers.
- A **pending activation** (limit/stop hit) creates: 1 deal + 1 position; the
  pending order ticket disappears from `orders_get()`.
- A **close** creates: 1 deal (opposite direction) and the position ticket
  disappears. In **hedging** mode an opposite position may appear; in
  **netting** the position is reduced/closed.
- **Deals are immutable history** — the only trustworthy record after the fact.
- `orders_get()` only shows *currently alive* pending orders — an order that
  filled/expired/cancelled disappears from it.

### 5.2 Reconciliation algorithm (`reconcile()`)

Given an intent (or a set of intents since last reconcile):

```
1. Fetch orders_get()   → alive pending orders (filter magic, our comment)
2. Fetch positions_get() → open positions (filter magic)
3. Fetch deals_get()     → deals since last_reconcile_ts (filter magic)
4. For each intent in {UNKNOWN_OUTCOME, SUBMITTED, PARTIALLY_FILLED}:
   a. comment lookup in orders → pending still alive → SUBMITTED
   b. comment lookup in deals  → deal(s) found →
        sum filled volume by deal side vs requested → FILLED or PARTIALLY_FILLED
   c. position lookup (symbol+magic) with matching open volume →
        map to position ticket for SL/TP management (§6)
   d. neither found →
        if intent was UNKNOWN_OUTCOME → FAILED_UNKNOWN (operator)
        if intent was SUBMITTED > expiry threshold → EXPIRED (pending expired)
   e. update persisted intent state; emit reconcile event
5. Return a ReconcileReport (per-intent state + open positions + deals)
```

### 5.3 Source-of-truth ordering

`deals > positions > orders` for *what happened*; `positions` for *what is open
now*; `orders` only for *what is still pending*. Never infer a fill from
`order_send()` alone; never infer a live position from a deal alone (deal may
have been a close).

---

## 6. SL/TP — attach, modify, persist

### 6.1 Model

`OrderIntent` carries optional `sl` / `tp` (prices, or `None`). For demo Phase 3:

- **Market entry**: SL/TP attached in the same `order_send()` (MT5 supports
  `sl`/`tp` on the request for market orders). Verified post-send via
  `positions_get()`.
- **Pending entry**: SL/TP are part of the pending order request. On
  activation, MT5 transfers them to the position automatically (server-side).
- **Modify**: `order_modify()`/`position_modify()` for SL/TP changes from the
  strategy layer. All modifications go through the same mode guard + magic
  filter; only positions with our magic may be modified.
- **Persistence**: the execution store records the *intended* SL/TP on the
  intent; `reconcile()` compares against actual position SL/TP and reports
  drift (does not silently overwrite).

### 6.2 Safety rule

SL/TP are **always** attached at entry for demo Phase 3 — a naked position
(no SL) is a configuration error (`MissingStopError`) unless the strategy
explicitly opted out and the operator approved. Rationale: we are touching
money, even demo; the habit must be correct from day one.

---

## 7. Close position

### 7.1 Close API

Closing = sending an opposite-direction market order for the position volume:

- **Netting mode**: `order_send()` with `ORDER_TYPE_BUY/SELL` opposite,
  `position=ticket` (the position ticket), `volume` ≤ open volume.
- **Hedging mode**: same, `position=ticket` — MT5 closes that specific
  position; excess volume opens a new opposite position (never happens with
  our exact-volume rule).
- Partial close: allowed only if `volume < open volume` and strategy requests
  it explicitly. Phase 3 default is **full close only**; partial close is
  `NotImplementedError` until requested.

### 7.2 Close verification

Same discipline as entry: retcode + `reconcile()` (deal with opposite side,
position ticket gone or reduced). A close is "done" when the position
disappears (or reduces by exactly the close volume) and a matching deal
exists — not when `order_send()` says `DONE`.

### 7.3 Protective close

If the process is shutting down or the strategy is being torn down with an
open position, the supervisor must attempt a protective close *only if*
configured (`auto_close_on_shutdown=false` default — no implicit money
movement). Default: leave position open, log + alert, do not close.

---

## 8. Reconciliation after restart

### 8.1 What survives

The execution store (`paper_runs.db`-style SQLite, but a **separate table
namespace** for MT5 execution) persists per-intent:

```
request_id (comment)  magic  symbol  side  kind  requested_volume
state (SUBMITTED/FILLED/PARTIALLY_FILLED/UNKNOWN_OUTCOME/FAILED/EXPIRED/CANCELLED)
sl/tp  open_position_ticket  deal_tickets[]  created_at  updated_at
```

### 8.2 Startup sequence

On every process start (before any new intent is sent):

1. Load all intents with `state in {SUBMITTED, PARTIALLY_FILLED,
   UNKNOWN_OUTCOME}` from the store.
2. Run full `reconcile()` (§5.2) over them.
3. Outcomes:
   - `FILLED` → if the strategy is alive, hand the fill back (callback/event);
     if not, record it — next strategy startup sees the open position via
     `positions_get()` and adopts it (magic match).
   - `UNKNOWN_OUTCOME` that now resolves → resolve to actual state.
   - Still unresolved → `FAILED_UNKNOWN`, operator alert.
4. **Adopt open positions**: all open MT5 positions with our magic (including
   ones the process never saw — e.g. created during downtime) are loaded as
   `PositionSnapshot`s into the execution state. This is the crash-recovery
   guarantee: *whatever is open with our magic is ours to manage.*
5. Only after steps 1–4 complete may new intents be submitted.

### 8.3 Crash mid-`order_send()`

The comment idempotency (§4.3.2) covers this: after restart, the comment
lookup finds the deal/order created by the half-completed send and resolves
the intent without re-submitting.

---

## 9. State machine (single normative model)

```
                    order_check pass
 INTENT_NEW ─────────────────────────────► SUBMITTED (pending placed)
     │                                          │
     │ order_send retcode DONE (market/pending  │ activate/fill (reconcile)
     │  activation)                              ▼
     ▼                                          FILLED ◄──┐
    FILLED (direct)                                   │    │ partial remainder fills
     │                                                │    │
     │ reconcile: remainder live                     │    │
     ▼                                                │    │
 PARTIALLY_FILLED ────────────────────────────────────┘    │
     │  cancel remainder (strategy)                        │
     ▼                                                      │
 CANCELLED (remainder)                                     │
     │                                                      │
     │  timeout/unknown ──► UNKNOWN_OUTCOME ──► reconcile ─┘
     │                                       │ not found
     ▼                                       ▼
 EXPIRED / FAILED                     FAILED_UNKNOWN (operator)
```

Transitions are driven **only by `reconcile()` evidence or explicit operator
action**, never by `order_send()` alone.

---

## 10. API surface (Phase 3, to be implemented after approval)

```python
class Mt5ExecutionAdapter(ExecutionAdapter):
    # Phase 2 (read-only, merged)
    connect / account_info / market_tick / resolve_symbol /
    positions / orders / mode_guard

    # Phase 3 (write path — ONLY after this contract is approved)
    def submit(self, intent: OrderIntent) -> ExecutionResult: ...
    def modify_sl_tp(self, position_ticket: int, sl: float, tp: float) -> ExecutionResult: ...
    def close_position(self, position_ticket: int) -> ExecutionResult: ...
    def reconcile(self, since_ts: datetime | None = None) -> ReconcileReport: ...
    def startup_reconcile(self) -> ReconcileReport: ...   # §8.2
```

All write methods: mode-guarded (demo only), idempotency-checked (§4),
retcode-classified (§1), and followed by `reconcile()`.

---

## 11. Open questions for Tech Lead (decision needed before implementation)

1. **Auto-close on shutdown**: default `false` (position left open, logged) —
   confirm.
2. **Partial-fill remainder**: strategy decides; no auto-replace — confirm.
3. **UNKNOWN_OUTCOME resolution**: operator action required; no auto-retry —
   confirm.
4. **SL/TP mandatory at entry** for Phase 3 (no naked positions) — confirm.
5. **Full close only** in Phase 3 (partial close deferred) — confirm.

---

*End of contract. Implementation of `order_send()` begins only after Tech Lead
approval of this document (and answers to §11).*
