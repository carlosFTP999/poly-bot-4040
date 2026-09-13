# Config Management Specification

## Purpose

Load strategy parameters and environment variables into typed configuration, using `Decimal` for monetary precision and supporting `LIVE_ENABLED`/`DRY_RUN` mode selection.

## Requirements

### Requirement: Strategy Parameters as Decimal

All monetary strategy parameters MUST be loaded as `Decimal` values: `PRICE_THRESHOLD` (0.40), `MAX_PER_SIDE` (2.00), `TOTAL_CAP` (4.00). `SHARE_FLOOR` MUST be `int` (5).

#### Scenario: Decimal precision

- GIVEN `PRICE_THRESHOLD=0.40` in config
- WHEN the config is loaded
- THEN `PRICE_THRESHOLD` is `Decimal("0.40")`, not `float`

#### Scenario: Integer share floor

- GIVEN `SHARE_FLOOR=5` in config
- WHEN the config is loaded
- THEN `SHARE_FLOOR` is `int(5)`

### Requirement: Environment Variable Loading

The system SHALL load configuration from environment variables with sensible defaults. Required env vars: `LIVE_ENABLED` (bool, default False), `DRY_RUN` (bool, default True), `GAMMA_BASE_URL`, `CLOB_BASE_URL`, `WS_URL` (default `wss://ws-subscriptions-clob.polymarket.com/ws/user`), `LOG_LEVEL` (default `INFO`, normalized to upper), `FUNDER` (default empty, fallback to `POLYMARKET_PROXY_ADDRESS`).

#### Scenario: Default dry-run mode

- GIVEN no environment variables are set
- WHEN the config is loaded
- THEN `DRY_RUN=True` and `LIVE_ENABLED=False`

#### Scenario: Live mode with keys

- GIVEN `LIVE_ENABLED=true`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `POLYMARKET_PRIVATE_KEY` are all set
- WHEN the config is loaded
- THEN `LiveClobExecutor` can be constructed without error

### Requirement: Mode Selection

The system MUST support three valid mode combinations: `DRY_RUN=True` only uses `DryRunExecutor`; `LIVE_ENABLED=True` only uses `LiveClobExecutor`; `LIVE_ENABLED=True + DRY_RUN=True` uses `PaperLiveExecutor` (PAPER_LIVE mode, requires all API keys). Both being `False` is invalid.

#### Scenario: Valid dry-run mode

- GIVEN `DRY_RUN=True` and `LIVE_ENABLED=False`
- WHEN mode is selected
- THEN `DryRunExecutor` is instantiated

#### Scenario: Paper-live mode

- GIVEN `DRY_RUN=True` and `LIVE_ENABLED=True` with all API keys set
- WHEN mode is selected
- THEN `PaperLiveExecutor` is instantiated

#### Scenario: Invalid dual mode

- GIVEN `DRY_RUN=False` and `LIVE_ENABLED=False`
- WHEN mode is validated
- THEN a configuration error is raised

### Requirement: API Key Types

`POLYMARKET_PRIVATE_KEY`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `POLYMARKET_PROXY_ADDRESS`, and `POLYMARKET_FUNDER` MUST be loaded as strings. `SIGNATURE_TYPE` MUST be `int` (default 2 — `GNOSIS_SAFE` for browser wallets; `0=EOA`, `1=POLY_PROXY`, `3=DEPOSIT_WALLET`).

#### Scenario: Signature type default

- GIVEN `SIGNATURE_TYPE` is not set in environment
- WHEN the config is loaded
- THEN `SIGNATURE_TYPE` is `int(2)`

### Requirement: Logging and WebSocket URL

`LOG_LEVEL` MUST be loaded as upper-cased string (default `INFO`) and `WS_URL` as string (default `wss://ws-subscriptions-clob.polymarket.com/ws/user`). Both SHALL be overridable via environment.

#### Scenario: WS_URL default

- GIVEN `WS_URL` is not set in environment
- WHEN the config is loaded
- THEN `WS_URL` is `wss://ws-subscriptions-clob.polymarket.com/ws/user`

#### Scenario: LOG_LEVEL normalization

- GIVEN `LOG_LEVEL=debug` in environment
- WHEN the config is loaded
- THEN `LOG_LEVEL` is `DEBUG`

### Requirement: Config Immutability

Once loaded, the config object MUST NOT be mutable at runtime. All values are read-only after construction.

#### Scenario: Runtime mutation blocked

- GIVEN a loaded config with `PRICE_THRESHOLD=Decimal("0.40")`
- WHEN code attempts `config.PRICE_THRESHOLD = Decimal("0.50")`
- THEN an error is raised or the assignment is rejected

### Requirement: FUNDER Separate Environment Variable

`POLYMARKET_FUNDER` MUST be loaded as a separate environment variable (not an alias for `POLYMARKET_PROXY_ADDRESS`). If empty, `POLYMARKET_PROXY_ADDRESS` is used as the fallback funder address for the `ClobClient`.

#### Scenario: FUNDER set

- GIVEN `POLYMARKET_FUNDER=0xabc123` in environment
- WHEN the config is loaded
- THEN `FUNDER` is `"0xabc123"` and is passed to `ClobClient`

#### Scenario: FUNDER empty, fallback

- GIVEN `POLYMARKET_FUNDER` is unset and `POLYMARKET_PROXY_ADDRESS=0xdef456`
- WHEN the executor is constructed
- THEN `POLYMARKET_PROXY_ADDRESS` is used as the funder

### Requirement: Clock Synchronization

`LiveClobExecutor` MUST use `ClockSync` for offset-based clock synchronization with the CLOB `/time` endpoint. The clock recalibrates every 5 minutes (CALIBRATION_INTERVAL=300s). On 401 timestamp errors, `force_recalibrate()` is called and the operation is retried once.
