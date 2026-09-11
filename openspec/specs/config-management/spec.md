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

The system SHALL load configuration from environment variables with sensible defaults. Required env vars: `LIVE_ENABLED` (bool, default False), `DRY_RUN` (bool, default True), `GAMMA_BASE_URL`, `CLOB_BASE_URL`.

#### Scenario: Default dry-run mode

- GIVEN no environment variables are set
- WHEN the config is loaded
- THEN `DRY_RUN=True` and `LIVE_ENABLED=False`

#### Scenario: Live mode with keys

- GIVEN `LIVE_ENABLED=true`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `POLYMARKET_PRIVATE_KEY` are all set
- WHEN the config is loaded
- THEN `LiveClobExecutor` can be constructed without error

### Requirement: Mode Selection

The system MUST support two mutually exclusive modes: `DRY_RUN=True` uses `DryRunExecutor`; `LIVE_ENABLED=True` uses `LiveClobExecutor`. Both being `True` is invalid; both being `False` is invalid. Exactly one MUST be `True`.

#### Scenario: Valid dry-run mode

- GIVEN `DRY_RUN=True` and `LIVE_ENABLED=False`
- WHEN mode is selected
- THEN `DryRunExecutor` is instantiated

#### Scenario: Invalid dual mode

- GIVEN `DRY_RUN=True` and `LIVE_ENABLED=True`
- WHEN mode is validated
- THEN a configuration error is raised

### Requirement: API Key Types

`POLYMARKET_PRIVATE_KEY`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, and `POLYMARKET_PROXY_ADDRESS` MUST be loaded as strings. `SIGNATURE_TYPE` MUST be `int` (default 3).

#### Scenario: Signature type default

- GIVEN `SIGNATURE_TYPE` is not set in environment
- WHEN the config is loaded
- THEN `SIGNATURE_TYPE` is `int(3)`

### Requirement: Config Immutability

Once loaded, the config object MUST NOT be mutable at runtime. All values are read-only after construction.

#### Scenario: Runtime mutation blocked

- GIVEN a loaded config with `PRICE_THRESHOLD=Decimal("0.40")`
- WHEN code attempts `config.PRICE_THRESHOLD = Decimal("0.50")`
- THEN an error is raised or the assignment is rejected
