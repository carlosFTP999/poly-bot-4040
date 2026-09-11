# Proposal: BTC 5-Minute Hedge Accumulation Bot

## Intent

Build an automated trading bot for Polymarket's BTC 5-min UP/DOWN markets that exploits price fluctuations by accumulating hedges on both sides (YES and NO tokens). The bot exploits the structural inefficiency where both sides settle at $0.40, guaranteeing $1.00 return per winning side on a $0.40 purchase — yielding +$1.00 per complete hedge.

## Scope

### In Scope
- Market discovery via Gamma API (`btc-updown-5m-{timestamp}`)
- Batch order placement (10 GTC limit orders: 5 YES + 5 NO at $0.40) via `POST /orders`
- WebSocket private monitoring for real-time fill tracking (~88ms latency)
- Phase rotation: wait full 300s window → `DELETE /cancel-all` → advance +300s
- DryRunExecutor (paper trading) and LiveClobExecutor (live) with DI via Executor Protocol
- Config via env vars with Decimal precision, LIVE_ENABLED/DRY_RUN modes
- Rate limit handling with exponential backoff (order/cancel tiers independent)
- Unit tests per module with injected callables (no real network in tests)

### Out of Scope
- Price prediction / ML models
- Ladder strategies (price stepping)
- Multi-market parallel operation
- Persisted state (JSON/SQLite) — deferred to future iteration
- pUSD/USDC.e wrapping logic — assume pUSD balance available

## Capabilities

### New Capabilities
- `market-discovery`: Gamma API integration to find active BTC 5-min markets by timestamp alignment
- `order-execution`: Batch GTC limit order placement via CLOB API v2 with Executor Protocol DI
- `ws-monitoring`: Private WebSocket connection for real-time order_update events with reconnection
- `engine-rotation`: 300s window lifecycle management (Phase1 → wait → Phase2 → rotate)
- `config-management`: Env-based Decimal config with LIVE_ENABLED/DRY_RUN mode selection

### Modified Capabilities
None — fresh project, no existing specs.

## Approach

Modular async architecture: 7 files (config, market, executor, engine, websocket, types, main). Executor Protocol provides DI for testability — `place_order()` and `cancel_all()` are injectable callables. Engine orchestrates the full cycle: discover → connect WS → place batch → wait 300s → cancel-all → rotate. WebSocket handles auth, reconnection with re-subscription, and fill event processing.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/config.py` | New | Decimal strategy params, env var loading, mode selection |
| `src/market.py` | New | Gamma API discover(), window_ts(), condition_id extraction |
| `src/executor.py` | New | Executor Protocol, DryRunExecutor, LiveClobExecutor |
| `src/engine.py` | New | 300s lifecycle, Phase1/Phase2 orchestration |
| `src/websocket.py` | New | Private WS auth, reconnection, order_update processing |
| `src/types.py` | New | Fill, Order, Market dataclasses |
| `src/main.py` | New | Entry point, mode selection, loop |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| WebSocket reconnection drops events | Med | Re-sync via GET /orders on reconnect; subscribe before placing orders |
| Rate limit hit across order/cancel tiers | Low | Independent tier handling; 10 orders/300s well under limits |
| Clock drift causes wrong window detection | Med | Use Gamma API server timestamps for alignment |
| Orphaned GTC orders persist across windows | High | Phase2 always runs cancel-all; verify with GET /orders post-cancel |
| Partial fills leave one-sided exposure | Med | Accept risk; strategy accounts for single-side completion |

## Rollback Plan

Disable bot via `LIVE_ENABLED=false` (default). Kill process. Cancel any remaining GTC orders manually via `DELETE /cancel-all` endpoint or Polymarket UI. Remove `src/` files and revert to pre-implementation commit.

## Dependencies

- `py-sdk` (py_clob_client) — CLOB API client
- `websockets` — Private WebSocket connection
- `httpx` — Async HTTP for Gamma API
- Polymarket API keys + private key (for live mode)

## Success Criteria

- [ ] Bot discovers active BTC 5-min market via Gamma API
- [ ] Phase1 places 10 GTC limit orders in single batch POST
- [ ] WebSocket receives fill events in real-time
- [ ] Phase2 cancels all pending orders via DELETE /cancel-all
- [ ] Window rotation advances +300s from aligned start
- [ ] DryRunExecutor produces deterministic fills without network
- [ ] All unit tests pass with DI (no real API calls)