# Poly-bot-4040 — BTC 5m Hedge Bot

Bot de trading automatizado para el mercado **BTC Up or Down — 5 minutos** de [Polymarket](https://polymarket.com). Implementa una estrategia de **hedge acumulativo**: compra en ambos lados (YES y NO) al mismo precio fijo para explotar fluctuaciones de precio dentro de cada ventana de 5 minutos.

> [!warning] Plan original
> El documento histórico `Plan-inicial.md` se conserva sin modificaciones. La fuente de verdad ejecutable es el código en `src/` y este README.

## Estrategia resumida

Cada ventana de 300s alineada a múltiplos de `300` (`window_ts = (now // 300) * 300`):

1. **Discover** — `Gamma API` con slug `btc-updown-5m-{window_ts}`. Si no hay mercado, retry 5s (0.5s interval) y luego rota +300s.
2. **WebSocket** — conecta `PrivateWebSocket` al `condition_id` descubierto (fail-graceful, 3 reintentos con backoff).
3. **Balance check** — `GET /balance-allowance` (CLOB, L2 `ClobClient`, `asset_type=pUSD`). Si `balance < TOTAL_CAP` rota.
4. **Phase 1** — coloca **10 órdenes GTC** limit `BUY` a precio fijo: **5 YES + 5 NO a $0.40**, `size = SHARE_FLOOR = 5` shares c/u. El `LiveClobExecutor` usa `py_clob_client` (`ClobClient`, `OrderArgs`, `chain_id=137`); el batch queda por debajo del límite de 15 órdenes/request del CLOB.
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
| `WS_URL` | str | `wss://ws-clob.polymarket.com` | URL WebSocket privado CLOB. |
| `LOG_LEVEL` | str | `INFO` | Nivel de logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Se normaliza a upper. |
| `POLYMARKET_PRIVATE_KEY` | str | `""` | Private key hex (`0x...`) para firmar órdenes L2. |
| `POLYMARKET_API_KEY` | str | `""` | API key CLOB (derivada). |
| `POLYMARKET_API_SECRET` | str | `""` | API secret CLOB. |
| `POLYMARKET_API_PASSPHRASE` | str | `""` | API passphrase CLOB. |
| `POLYMARKET_PROXY_ADDRESS` | str | `""` | Proxy/funder address (Gnosis Safe / deposit wallet). Se pasa como `funder` al `ClobClient`. |
| `SIGNATURE_TYPE` | int | `2` | `0=EOA`, `1=POLY_PROXY`, `2=GNOSIS_SAFE` (browser wallets), `3=DEPOSIT_WALLET`. Default `2`. |
| `PRICE_THRESHOLD` | Decimal | `0.40` | Precio límite fijo por orden (Phase 1). |
| `MAX_PER_SIDE` | Decimal | `2.00` | Máximo por lado (5 × $0.40). Referencial. |
| `TOTAL_CAP` | Decimal | `20.00` | Balance mínimo pUSD requerido para operar una ventana. Gate en `Engine` (`balance < TOTAL_CAP → rotate`). |
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

1. Completar `.env` con `POLYMARKET_PRIVATE_KEY` y `POLYMARKET_PROXY_ADDRESS`.
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
- `LIVE_ENABLED=True` → `LiveClobExecutor` (L2 `ClobClient` con `host`, `chain_id=137`, `key`, `creds=ApiCreds`, `signature_type`, `funder`).

Balance check en LIVE usa `ClobClient.get_balance_allowance(BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=...))` y compara contra `TOTAL_CAP`.

## Tests

```bash
python -m pytest tests/ -q
# 101 tests
```

Cobertura por módulo (inyectando dependencias, sin red):

- `test_config` — defaults, env loading, validación de modo, `SIGNATURE_TYPE`/`LOG_LEVEL`/`WS_URL`.
- `test_market` — `current_window_ts`, `discover` con client inyectado, `clobTokenIds`/`outcomes` stringificados.
- `test_executor` — `DryRunExecutor` determinismo, `LiveClobExecutor` validación de keys y `ClobClient` L2.
- `test_engine` — ciclo completo con `now` inyectable, wait 300s obligatorio, cancel-all, rotate.
- `test_early_entry` / `test_prewarm` — `MAX_LATE_S=15` skip y `PREWARM_BEFORE_END_S=5` sin colocar órdenes.
- `test_websocket` / `test_types` / `test_main` — auth, subscribe, `order_update` `MATCHED`, `select_executor`.

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
├── config.py     # Settings frozen dataclass, Decimal para montos, load_settings_from_env(), validación LIVE/DRY_RUN, defaults: TOTAL_CAP=20, SHARE_FLOOR=5, PRICE_THRESHOLD=0.40, SIGNATURE_TYPE=2, WS_URL, LOG_LEVEL
├── market.py     # current_window_ts(now) = (now // 300)*300, discover(client, window_ts, gamma_base_url) con slug btc-updown-5m-{ts}, parsing tokens[] y clobTokenIds/outcomes stringificados
├── executor.py   # Protocol Executor { place_limit_order, cancel_all }, DryRunExecutor (deque _resting, fills simulados), LiveClobExecutor (L2 ClobClient, OrderArgs, create_order/post_order, cancel_all → DELETE /cancel-all)
├── engine.py     # Engine — ciclo 300s: discover (+retry 5s/0.5s), WS connect, balance_check vs TOTAL_CAP, MAX_LATE_S=15 skip, Phase1 10 órdenes, wait full 300s (prewarm 5s), Phase2 cancel_all, rotate; deps inyectables (executor, discover, ws_connect, balance_check, now, sleep)
├── main.py       # Entry point: load_settings, select_executor, connect_websocket (skip en DRY_RUN), balance_check (ClobClient L2 + BalanceAllowanceParams COLLATERAL), run_bot() → Engine.run_cycle()
├── websocket.py  # PrivateWebSocket — auth (apiKey/secret/passphrase), subscribe {operation:"subscribe", markets:[condition_id]}, _listen/_process_event (type/status mayúsculos), _extract_fill solo si status==MATCHED, reconnect 3 intentos + _sync_orders
└── types.py      # Dataclasses frozen: OrderStatus, TokenInfo, Order (GTC, expiration=0), Fill, MarketInfo (condition_id, token_yes/no_id, start/end), WindowState
```

Otros archivos relevantes:

- `derive_credentials.py` — deriva `API_KEY/SECRET/PASSPHRASE` vía `ClobClient.create_or_derive_api_creds()` con `signature_type=2` y `funder=PROXY_ADDRESS`.
- `deploy/poly-bot.service` — unit systemd LIVE.
- `tests/` — 101 tests pytest (ver Tests).
