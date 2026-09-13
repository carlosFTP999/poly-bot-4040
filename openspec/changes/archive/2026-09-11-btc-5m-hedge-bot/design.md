# Design: BTC 5-Minute Hedge Accumulation Bot

## Technical Approach

Modular async Python bot implementing a hedge accumulation strategy on Polymarket's BTC 5-min UP/DOWN markets. The system discovers markets via Gamma API, places 2 GTC limit orders (1 YES + 1 NO at $0.40) in a single batch, monitors fills via private WebSocket (auth+subscribe combined frame), waits the full 300s window, cancels all pending orders, and rotates to the next window. Executor Protocol enables dependency injection for testability (DryRunExecutor for paper trading, LiveClobExecutor for live trading, PaperLiveExecutor for zero-risk live infrastructure testing).

## Architecture Decisions

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| **Dependency Injection** | Injectable callables (executor, discover, ws_connect, now, balance_check) | Global singletons, service locator | Enables deterministic unit testing without network; matches spec requirement |
| **Monetary Precision** | `Decimal` for all strategy parameters and amounts | `float` | Avoids floating-point rounding; Polymarket prices are exact decimals |
| **Executor Pattern** | Protocol with DI, not inheritance hierarchy | Abstract base class | Python Protocol provides structural typing; simpler mocking |
| **Async/Await** | Full async for all API calls | Sync with threading | WebSocket requires async; unified model simplifies code |
| **State Management** | No persistent state (first iteration) | SQLite/JSON | Keeps scope minimal; orphaned orders handled via cancel-all rotation |
| **Rate Limit Handling** | Exponential backoff (1s/2s/4s) with Retry-After extraction | Fixed delay | Respects separate order/cancel buckets; 429/425/503 specific handling |
| **Clock Synchronization** | Offset-based via CLOB /time + 5min recalibration + 401 retry | Local `time.time()` | Prevents timestamp auth failures; auto-recalibrate on 401 |
| **WebSocket Auth** | Combined auth+subscribe frame `{"auth":{...},"type":"user","markets":[...]}` | Separate auth + subscribe | Single frame reduces connection setup latency |
| **Order Batch Size** | 2 orders (1 YES + 1 NO) per window | 10 orders (5 YES + 5 NO) | Reduced capital exposure; TOTAL_CAP=4.00 |

## Module Dependency Graph

```
main.py
  ├── config.py (loads Settings)
  ├── executor.py (select_executor)
  └── engine.py (run_cycle)
        ├── market.py (discover, current_window_ts)
        ├── executor.py (place_limit_order, cancel_all)
        └── websocket.py (connect, subscribe, on_order_update)

types.py ← used by all modules
```

## Data Flow

```
Window Start (t=0s)
    │
    ▼
config.Settings ─────────────────────────────┐
    │                                         │
    ▼                                         ▼
market.discover(client) ──→ MarketInfo ──→ websocket.connect(auth+subscribe)
    │                                         │
    │                                         ▼
    │                                   _subscribe_initial() combined frame
    │                                   PING/PONG heartbeat every 10s
    ▼                                         ▼
engine.balance_check() ──→ pUSD >= $4.00? ──→ executor.place_limit_order ×2 (1 YES + 1 NO)
    │                                         │
    │                                         ▼
    │                                   WebSocket order_update events
    │                                   (fill tracking)
    ▼                                         ▼
asyncio.sleep(window_end - now) ──────── executor.cancel_all()
    │
    ▼
window_ts += 300 → next window
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/types.py` | Create | Dataclasses: Order, Fill, MarketInfo, WindowState, OrderStatus |
| `src/config.py` | Create | Frozen dataclass Settings with Decimal fields, env loading |
| `src/market.py` | Create | current_window_ts(), discover(client), token extraction |
| `src/executor.py` | Create | Executor Protocol, DryRunExecutor, LiveClobExecutor |
| `src/engine.py` | Create | Engine class with injectable deps, run_cycle() |
| `src/websocket.py` | Create | WebSocketClient: auth+subscribe combined frame, _subscribe_initial(), PING/PONG heartbeat, reconnect + _sync_orders (L2 GET /orders), process events |
| `src/main.py` | Create | Entry point, config load, executor selection, engine loop |
| `src/__init__.py` | Create | Package marker |

## Interfaces / Contracts

```python
# types.py
@dataclass(frozen=True)
class MarketInfo:
    condition_id: str
    token_yes_id: str
    token_no_id: str
    start_date: int  # unix seconds
    end_date: int

@dataclass(frozen=True)
class Fill:
    token_id: str
    side: str  # "YES" | "NO"
    price: Decimal
    size: int
    timestamp: int

# executor.py
class Executor(Protocol):
    async def place_limit_order(self, token_id: str, side: str, price: Decimal, size: int) -> Fill: ...
    async def cancel_all(self) -> None: ...

# engine.py
class Engine:
    def __init__(
        self,
        executor: Executor,
        discover: Callable[[Callable], Awaitable[Optional[MarketInfo]]],
        ws_connect: Callable[[str, Callable], Awaitable[None]],
        balance_check: Callable[[], Awaitable[Decimal]],
        now: Callable[[], int],
        config: Settings,
    ) -> None: ...
    async def run_cycle(self) -> None: ...
```

## Error Handling Strategy

| Error Type | Handling | Recovery |
|------------|----------|----------|
| **429 Rate Limit** | Exponential backoff (1s, 2s, 4s) with Retry-After header extraction | Retry up to 3x per call |
| **425 Too Early** | Exponential backoff (1s, 2s, 4s) | Retry up to 3x |
| **503 Service Unavailable** | Exponential backoff (1s, 2s, 4s) | Retry up to 3x |
| **401 Auth / Timestamp** | Clock re-sync via `_post_with_clock_retry`, one retry | Automatic re-sync |
| **400 Bad Request** | Log order details, skip batch | Continue window rotation |
| **WebSocket Disconnect** | Reconnect + _subscribe_initial() + GET /orders L2 sync | 3 attempts then stop |
| **Balance < $4.00** | Skip window, rotate +300s | Automatic next window |
| **Market Not Found** | Rotate +300s, retry next window | Automatic retry |

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| **Unit - config** | Decimal precision, env loading, mode validation | Mock env vars, assert types |
| **Unit - market** | window_ts calculation, token extraction, injectable client | Mock client returning fixture JSON |
| **Unit - executor** | DryRunExecutor determinism, LiveClobExecutor key validation | Mock py_clob_client; no network |
| **Unit - engine** | Full cycle with mocked deps; wait always 300s; balance check | Inject now=lambda, discover=lambda, executor=Mock |
| **Unit - websocket** | Reconnection logic, event parsing, auth+subscribe frame, _subscribe_initial(), PING/PONG heartbeat, _sync_orders | Mock WebSocket server |
| **Unit - clock_sync** | Offset calibration, 5min recheck, force_recalibrate, 401 retry | Mock httpx |
| **Integration** | Engine + executor + market (mocked ws) | Simulated window lifecycle |
| **E2E (manual)** | DryRunExecutor mode, live mode with small balance | Real Gamma API, paper trading |

**What to mock**: HTTP clients, WebSocket connections, py_clob_client, time functions, httpx.
**What to test with real calls**: Only manual E2E with DryRunExecutor.

## External Library Integration Points

| Library | Module | Integration |
|---------|--------|-------------|
| `py_clob_client` | executor.py (LiveClobExecutor) | Order placement (OrderArgs + OrderType.GTC), cancel-all, balance check, batch POST /orders |
| `websockets` | websocket.py | Private WS connection, auth+subscribe combined frame, PING/PONG heartbeat |
| `httpx` | market.py, clock_sync.py | Async HTTP to Gamma API, sync HTTP to CLOB /time |
| `asyncio` | engine.py, main.py, websocket.py | Event loop, sleep, task coordination, heartbeat scheduling |
| `decimal` | config.py, types.py | All monetary values |
| `os` | config.py | Environment variable loading |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary.

## Migration / Rollout

No migration required. Fresh project with no existing code. Feature flag: `LIVE_ENABLED=false` (default) ensures safe rollout. Deploy to VPS near Polymarket servers (Amsterdam) for minimal latency.

## Open Questions

- [x] ~~Exact Gamma API response format for `tokens` array~~ — Confirmed: `clobTokenIds` + `outcomes` as JSON-stringified arrays
- [x] ~~WebSocket auth message format~~ — Confirmed: `{"auth":{...},"type":"user","markets":[...]}` combined frame
- [x] ~~Rate limit headers~~ — Confirmed: Retry-After extraction from error messages
- [x] ~~py-sdk `get_balance()` method signature~~ — Confirmed: `get_balance_allowance(BalanceAllowanceParams)` with `AssetType.COLLATERAL`
