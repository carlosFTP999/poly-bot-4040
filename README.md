# Poly-bot-4040 — BTC 5m Hedge Bot

Bot de trading automatizado para el mercado **BTC Up or Down — 5 minutos** de [Polymarket](https://polymarket.com). Implementa una estrategia de **hedge acumulativo**: compra en ambos lados (YES y NO) al mismo precio fijo para explotar fluctuaciones de precio dentro de cada ventana de 5 minutos.

> [!warning] Plan original
> El documento histórico `Plan-inicial.md` se conserva sin modificaciones. La fuente de verdad ejecutable es el código en `src/` y este README.

## Estrategia resumida

Cada ventana de 300s alineada a múltiplos de `300` (`window_ts = (now // 300) * 300`):

1. **Discover** — `Gamma API` con slug `btc-updown-5m-{window_ts}`. Si no hay mercado, retry 5s (0.5s interval) y luego rota +300s.
2. **WebSocket** — conecta `PrivateWebSocket` al `condition_id` descubierto (fail-graceful, 3 reintentos con backoff).
3. **Balance check** — `GET /balance-allowance` (CLOB, L2 `ClobClient`, `asset_type=pUSD`). Si `balance < TOTAL_CAP` rota.
4. **Phase 1** — coloca **2 órdenes GTC** limit `BUY` a precio fijo: **1 YES + 1 NO a $0.40**, `size = SHARE_FLOOR = 5` shares c/u. El `LiveClobExecutor` usa `py_clob_client` (`ClobClient`, `OrderArgs`, `chain_id=137`); batch de 2 órdenes con retry exponencial en 429/425/503.
5. **Wait** — espera **siempre** hasta `window_ts + 300` (nunca sale anticipado por fills). Pre-warm opcional del próximo mercado a `PREWARM_BEFORE_END_S = 5s` del cierre.
6. **Phase 2** — `DELETE /cancel-all` vía `executor.cancel_all()` — cancela todas las órdenes pendientes.
7. **Rotate** — avanza +300s y repite el ciclo.

## Regla de entrada

Solo entra a la ventana si llega con margen:

```
seconds_into_window = now - window_ts
si seconds_into_window > MAX_LATE_S (15s) → skip, rota a window_ts + 300
```

Es decir: **si ya pasaron más de 15s dentro de la ventana actual, no opera esa ventana y salta a la próxima**. Esto evita entrar tarde con tiempo insuficiente para que las órdenes hagan match.

Constante en `src/engine.py`:

```python
MAX_LATE_S = 15.0
```

## Setup

Requiere Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt`:

```
py-clob-client==0.34.6
websockets==15.0
python-dotenv==1.2.3
```

Copiar y editar entorno:

```bash
cp .env.example .env  # si existe
# o crear .env manualmente — ver tabla Config
chmod 600 .env
```

## Config

Todas las variables se cargan en `src/config.py` → `Settings` (frozen dataclass, `Decimal` para montos). `load_settings_from_env()` aplica defaults y valida modo.

| Variable | Tipo | Default | Descripción |
|---|---|---|---|
| `LIVE_ENABLED` | bool | `false` | Habilita modo live (requiere las 4 keys). Mutuamente excluyente con `DRY_RUN`. |
| `DRY_RUN` | bool | `true` | Paper trading con `DryRunExecutor` (sin red). |
| `CLOB_BASE_URL` | str | `https://clob.polymarket.com` | Base URL CLOB API v2. |
| `GAMMA_BASE_URL` | str | `https://gamma-api.polymarket.com` | Base URL Gamma API (discovery). |
| `WS_URL` | str | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | URL WebSocket privado CLOB (con auth+subscribe). |
| `LOG_LEVEL` | str | `INFO` | Nivel de logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Se normaliza a upper. |
| `POLYMARKET_PRIVATE_KEY` | str | `""` | Private key hex (`0x...`) para firmar órdenes L2. |
| `POLYMARKET_API_KEY` | str | `""` | API key CLOB (derivada). |
| `POLYMARKET_API_SECRET` | str | `""` | API secret CLOB. |
| `POLYMARKET_API_PASSPHRASE` | str | `""` | API passphrase CLOB. |
| `POLYMARKET_PROXY_ADDRESS` | str | `""` | Proxy address (Gnosis Safe / deposit wallet). Se pasa como `funder` al `ClobClient` cuando `FUNDER` no está configurado. |
| `POLYMARKET_FUNDER` | str | `""` | Funder address separado para el `ClobClient`. Si está vacío, se usa `POLYMARKET_PROXY_ADDRESS` como fallback. |
| `SIGNATURE_TYPE` | int | `2` | `0=EOA`, `1=POLY_PROXY`, `2=GNOSIS_SAFE` (browser wallets), `3=DEPOSIT_WALLET`. Default `2`. |
| `PRICE_THRESHOLD` | Decimal | `0.40` | Precio límite fijo por orden (Phase 1). |
| `MAX_PER_SIDE` | Decimal | `2.00` | Máximo por lado (1 × $0.40 × 5 shares = $2.00). |
| `TOTAL_CAP` | Decimal | `4.00` | Balance mínimo pUSD requerido para operar una ventana (2 órdenes × $2.00/side). Gate en `Engine` (`balance < TOTAL_CAP → rotate`). |
| `SHARE_FLOOR` | int | `5` | Shares mínimos por orden (mínimo CLOB GTC/GTD). |

Validación en `Settings.__post_init__`:

- `LIVE_ENABLED` y `DRY_RUN` no pueden ser ambos `True` ni ambos `False` (exactamente uno debe ser `True`).
- Si `LIVE_ENABLED=True`, las 4 keys deben estar presentes o lanza `ValueError`.

## Run

### DRY_RUN (default, sin fondos)

```bash
# con .env en DRY_RUN=true / LIVE_ENABLED=false
python -m src.main
```

Logs en stdout con formato `%(asctime)s - %(name)s - %(levelname)s - %(message)s` y nivel según `LOG_LEVEL`.

### LIVE

1. Completar `.env` con `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_PROXY_ADDRESS` y (opcional) `POLYMARKET_FUNDER`.
2. Derivar credenciales CLOB (una sola vez, no expiran):

```bash
python derive_credentials.py
# imprime POLYMARKET_API_KEY / SECRET / PASSPHRASE → copiar a .env
```

3. En `.env` setear:

```
LIVE_ENABLED=true
DRY_RUN=false
```

4. Ejecutar:

```bash
python -m src.main
```

`select_executor()` en `src/main.py` elige:

- `DRY_RUN=True` → `DryRunExecutor` (fills simulados, sin red).
- `LIVE_ENABLED=True` → `LiveClobExecutor` (L2 `ClobClient` con `host`, `chain_id=137`, `key`, `creds=ApiCreds`, `signature_type`, `funder`); `PAPER_LIVE` (ambos True) → `PaperLiveExecutor`.

Balance check en LIVE usa `LiveClobExecutor.get_balance_allowance()` → `ClobClient.get_balance_allowance(BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=...))` y compara contra `TOTAL_CAP`. En `PAPER_LIVE` se usa la misma llamada real.

## Tests

```bash
python -m pytest tests/ -q
# 122 tests
```

Cobertura por módulo (inyectando dependencias, sin red):

- `test_config` — defaults, env loading, validación de modo, `SIGNATURE_TYPE`/`LOG_LEVEL`/`WS_URL`, `FUNDER` como var separada.
- `test_market` — `current_window_ts(now: int)` pure function, `discover` con client inyectado, `clobTokenIds`/`outcomes` stringificados.
- `test_executor` — `DryRunExecutor` determinismo, `LiveClobExecutor` validación de keys y `ClobClient` L2, retry con backoff (D5), `OrderType.GTC` explícito (D6).
- `test_engine` — ciclo completo con `now` inyectable, wait 300s obligatorio, cancel-all, rotate, balance vs `TOTAL_CAP=4.00`.
- `test_early_entry` / `test_prewarm` — `MAX_LATE_S=15` skip y `PREWARM_BEFORE_END_S=5` sin colocar órdenes.
- `test_websocket` / `test_types` / `test_main` — auth+subscribe frame `{"auth":{...},"type":"user"}`, `_subscribe_initial()`, PING/PONG heartbeat cada 10s, `order_update` `MATCHED`, `_sync_orders` real con `GET /orders` (L2 headers), select_executor, `PAPER_LIVE`.
- `test_clock_sync` — ClockSync offset-based con `/time`, recalibración cada 5min, reintentos en 401.

## Deploy VPS

Systemd unit en `deploy/poly-bot.service`:

```ini
[Unit]
Description=Poly-bot-4040 BTC 5m Hedge Bot (LIVE)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=polybot
Group=polybot
WorkingDirectory=/opt/poly-bot-4040
EnvironmentFile=/opt/poly-bot-4040/.env
ExecStart=/opt/poly-bot-4040/.venv/bin/python -m src.main
Restart=always
RestartSec=10
StandardOutput=append:/var/log/poly-bot/bot.log
StandardError=append:/var/log/poly-bot/bot.log
[Install]
WantedBy=multi-user.target
```

Pasos:

```bash
# en el VPS (cercano a Amsterdam para latencia Polymarket)
sudo useradd -r -m -s /usr/sbin/nologin polybot
sudo mkdir -p /opt/poly-bot-4040 /var/log/poly-bot
sudo chown polybot:polybot /opt/poly-bot-4040 /var/log/poly-bot

# copiar repo
sudo -u polybot git clone <repo> /opt/poly-bot-4040
sudo -u polybot python3 -m venv /opt/poly-bot-4040/.venv
sudo -u polybot /opt/poly-bot-4040/.venv/bin/pip install -r /opt/poly-bot-4040/requirements.txt

# env
sudo -u polybot cp /opt/poly-bot-4040/.env.example /opt/poly-bot-4040/.env  # o crear
sudo chmod 600 /opt/poly-bot-4040/.env
sudo chown polybot:polybot /opt/poly-bot-4040/.env

# service
sudo cp deploy/poly-bot.service /etc/systemd/system/poly-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now poly-bot
sudo journalctl -u poly-bot -f
```

> `.env` debe tener permisos `600` y pertenecer a `polybot:polybot`.

## Estructura `src/`

```
src/
├── config.py     # Settings frozen dataclass, Decimal para montos, load_settings_from_env(), validación LIVE/DRY_RUN/PAPER_LIVE, defaults: TOTAL_CAP=4, SHARE_FLOOR=5, PRICE_THRESHOLD=0.40, SIGNATURE_TYPE=2, WS_URL, FUNDER (separate env var), LOG_LEVEL
├── market.py     # current_window_ts(now: int) pure function = (now // 300)*300, discover(client, window_ts, gamma_base_url) con slug btc-updown-5m-{ts}, parsing tokens[] y clobTokenIds/outcomes stringificados
├── executor.py   # Protocol Executor { place_limit_order, place_limit_orders_batch, cancel_all }, DryRunExecutor (deque _resting, fills simulados), LiveClobExecutor (L2 ClobClient, OrderArgs, OrderType.GTC explícito, post_order, cancel_all → DELETE /cancel-all, retry con backoff 429/425/503), PaperLiveExecutor (real L2 + balance, sin órdenes reales)
├── engine.py     # Engine — ciclo 300s: discover (+retry 5s/0.5s), WS connect, balance_check vs TOTAL_CAP=4.00, MAX_LATE_S=15 skip, Phase1 2 órdenes (1 YES + 1 NO), wait full 300s (prewarm 5s), Phase2 cancel_all, rotate; deps inyectables (executor, discover, ws_connect, balance_check, now, sleep)
├── websocket.py  # PrivateWebSocket — auth+subscribe frame {"auth":{...},"type":"user","markets":[...]}, _subscribe_initial() combinado, PING/PONG heartbeat cada 10s, _listen/_process_event (type/status), _extract_fill solo si status==MATCHED, reconnect 3 intentos con backoff, _sync_orders real con L2 ClobClient (GET /orders)
├── clock_sync.py # ClockSync — calibración offset-based con CLOB /time endpoint, recalibración cada 5min, force_recalibrate() para reintentos en 401
├── main.py       # Entry point: load_settings, select_executor (DRY_RUN/PAPER_LIVE/LIVE), connect_websocket, balance_check, run_bot() → Engine.run_cycle()
└── types.py      # Dataclasses frozen: OrderStatus, TokenInfo, Order (GTC, expiration=0), Fill, MarketInfo (condition_id, token_yes/no_id, start/end), WindowState
```

Otros archivos relevantes:

- `derive_credentials.py` — deriva `API_KEY/SECRET/PASSPHRASE` vía `ClobClient.create_or_derive_api_creds()` con `signature_type=2` y `funder=FUNDER` (o `PROXY_ADDRESS` como fallback).
- `deploy/poly-bot.service` — unit systemd LIVE.
- `tests/` — 122 tests pytest (ver Tests).
