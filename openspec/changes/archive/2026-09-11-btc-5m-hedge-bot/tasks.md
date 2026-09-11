# Tasks: BTC 5-Minute Hedge Accumulation Bot

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 700-900 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: types.py + config.py + tests; PR 2: market.py + executor.py + tests; PR 3: websocket.py + engine.py + main.py + tests |
| Delivery strategy | single-pr |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Foundation types and config | PR 1 | `pytest tests/test_types.py tests/test_config.py` | N/A (no network) | src/types.py, src/config.py |
| 2 | Market discovery and order execution | PR 2 | `pytest tests/test_market.py tests/test_executor.py` | N/A (mocked HTTP) | src/market.py, src/executor.py |
| 3 | WebSocket, engine, main | PR 3 | `pytest tests/test_websocket.py tests/test_engine.py tests/test_main.py` | N/A (mocked WS) | src/websocket.py, src/engine.py, src/main.py |

## Phase 1: Foundation

- [x] 1.1 Create `src/__init__.py` with package marker
- [x] 1.2 Create `src/types.py` with OrderStatus enum, Order, Fill, MarketInfo, WindowState dataclasses
- [x] 1.3 Write unit tests `tests/test_types.py` verifying frozen dataclasses and Decimal fields
- [x] 1.4 Create `src/config.py` with Settings frozen dataclass, Decimal fields, env loading
- [x] 1.5 Write unit tests `tests/test_config.py` verifying env loading, mode validation, immutability

## Phase 2: Core Implementation

- [x] 2.1 Create `src/market.py` with `current_window_ts()` function per spec scenario
- [x] 2.2 Add `discover()` function with injectable HTTP client, token extraction
- [x] 2.3 Write unit tests `tests/test_market.py` verifying window calculation and token extraction
- [x] 2.4 Create `src/executor.py` with Executor Protocol, DryRunExecutor, LiveClobExecutor
- [x] 2.5 Write unit tests `tests/test_executor.py` verifying DryRunExecutor determinism and key validation
- [x] 2.6 Create `src/websocket.py` with WebSocketClient: connect, auth, subscribe, reconnect, event processing
- [x] 2.7 Write unit tests `tests/test_websocket.py` verifying reconnection logic and event parsing

## Phase 3: Integration

- [x] 3.1 Create `src/engine.py` with Engine class, injectable dependencies, run_cycle()
- [x] 3.2 Write unit tests `tests/test_engine.py` verifying full cycle with mocked deps
- [x] 3.3 Create `src/main.py` entry point, config load, executor selection, engine loop
- [x] 3.4 Write unit tests `tests/test_main.py` verifying mode selection and startup

## Phase 4: Testing / Verification

- [x] 4.1 Integration test: Engine + executor + market (mocked ws) simulating window lifecycle
- [ ] 4.2 Manual E2E test with DryRunExecutor (paper trading) using real Gamma API
- [x] 4.3 Verify all specs pass: run `pytest tests/ -v` (88/88 passed)

## Phase 5: Cleanup

- [ ] 5.1 Update README with setup instructions and mode selection
- [x] 5.2 Add type hints and docstrings to all public functions
