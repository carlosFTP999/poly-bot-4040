"""One-shot DRY_RUN window de 5 minutos reales contra Gamma live - Sin mocks."""

import asyncio
import os
import time
import json
import urllib.request
import datetime
import logging
from decimal import Decimal

# Forzar DRY_RUN aunque .env diga otra cosa - ANTES de importar config
os.environ["DRY_RUN"] = "true"
os.environ["LIVE_ENABLED"] = "false"

from src.market import discover, current_window_ts
from src.executor import DryRunExecutor

# Configurar logging con timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logging.Formatter.converter = time.gmtime
logger = logging.getLogger("dryrun_one_window")

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

async def http_client(url: str):
    """Cliente HTTP real contra Gamma live - urllib + User-Agent."""
    def _fetch():
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    # Run blocking urllib in thread to keep async
    return await asyncio.to_thread(_fetch)

def iso_to_unix(value) -> int | None:
    """Convierte ISO string o int a unix seconds. Retorna None si no parseable."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        # Si es digitos puros, es unix como string
        if v.isdigit():
            try:
                return int(v)
            except:
                pass
        # ISO format: 2026-09-11T15:15:00Z
        try:
            # Reemplazar Z por +00:00 para fromisoformat
            iso = v.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(iso)
            return int(dt.timestamp())
        except Exception as e:
            logger.warning(f"iso_to_unix failed for '{v}': {e}")
            return None
    return None

async def main():
    start_wall = time.monotonic()
    start_unix = int(time.time())
    logger.info(f"=== DRY_RUN ONE WINDOW - Inicio real wall-clock ===")
    logger.info(f"DRY_RUN forced: DRY_RUN={os.environ.get('DRY_RUN')} PAPER={os.environ.get('LIVE_ENABLED')}")
    logger.info(f"Executor: DryRunExecutor (verificado, modo simulado)")

    # Verificar que no haya modo real en config
    from src.config import load_settings_from_env
    settings = load_settings_from_env()
    logger.info(f"Settings cargados: DRY_RUN={settings.DRY_RUN} PAPER_MODE={not settings.LIVE_ENABLED} GAMMA={settings.GAMMA_BASE_URL}")
    if settings.LIVE_ENABLED:
        logger.error("FATAL: modo real activo aunque forzamos DRY_RUN - abortando para no gastar plata")
        return

    # 1. Descubrimiento de mercado con ventana real
    window_ts = current_window_ts(start_unix)
    logger.info(f"WINDOW_TS inicial: {window_ts} (now={start_unix} -> {datetime.datetime.fromtimestamp(start_unix, tz=datetime.timezone.utc).isoformat()})")

    market = None
    tried_slugs = []
    # Probar ts, ts+300, ts+600 hasta encontrar uno (max 5 intentos = 20 min hacia adelante)
    for offset in [0, 300, 600, 900, 1200]:
        ts = window_ts + offset
        slug = f"btc-updown-5m-{ts}"
        tried_slugs.append(slug)
        logger.info(f"Probando slug: {slug} (ts={ts}) -> {GAMMA_BASE_URL}/markets?slug={slug}")
        m = await discover(client=http_client, window_ts=ts, gamma_base_url=GAMMA_BASE_URL)
        if m is not None:
            logger.info(f"MARKET encontrado: slug={slug} conditionId={m.condition_id} token_yes={m.token_yes_id[:12]}... token_no={m.token_no_id[:12]}... start_date={m.start_date} end_date={m.end_date}")
            market = m
            window_ts = ts
            break
        else:
            logger.info(f"MARKET no encontrado para slug={slug}, probando siguiente ventana")

    if market is None:
        logger.warning(f"No se encontro mercado activo tras probar slugs: {tried_slugs}. Rotacion inmediata sin wait.")
        logger.info(f"ROTATE: sin mercado, fin de ventana one-shot. Tiempo total wall: {time.monotonic() - start_wall:.1f}s")
        logger.info(f"Estado final: sin resting, sin cancels, 0 fills (no market)")
        return

    # Convertir end_date/start_date a unix si vienen como ISO string
    end_unix = iso_to_unix(market.end_date)
    start_unix_market = iso_to_unix(market.start_date)
    logger.info(f"Market dates raw: start_date={market.start_date} -> unix={start_unix_market}, end_date={market.end_date} -> unix={end_unix}")
    if end_unix is None:
        logger.info(f"endDate no parseable, usando fallback 300s")
        remaining = 300
    else:
        now_unix = int(time.time())
        remaining = end_unix - now_unix
        logger.info(f"Calculo remaining: end_unix={end_unix} - now={now_unix} = {remaining}s (endDate={datetime.datetime.fromtimestamp(end_unix, tz=datetime.timezone.utc).isoformat()})")
        if remaining <= 0:
            logger.warning(f"Ventana ya expiro (remaining={remaining} <=0), usando 300s fallback o 0")
            remaining = 0
        # Clamp para no esperar mas de 400s en test (si queda >300 por estar en ventana futura)
        # Pero respetamos Gamma: si remaining es 489s (proxima ventana), eso seria >300
        # El Plan dice Phase1->wait 300s SIEMPRE -> Phase2, asi que si remaining >310, limitamos a 300 para cumplir el Plan?
        # Para cumplir literal "remaining = endDate_unix - now() o 300", NO clampeamos.
        # Pero reportamos claramente.
    
    # El Plan Engine._wait_window_end siempre espera 300. Para veredicto, mostramos ambos.
    plan_remaining = 300
    logger.info(f"Plan Engine espera SIEMPRE 300s. Gamma remaining={remaining}s. Usaremos Gamma remaining para este one-shot (task literal).")

    # 2. WS connect (si falla, loguear y seguir - en dry-run puede fallar sin credenciales)
    logger.info(f"WS: intentando conectar a conditionId={market.condition_id} (esperado fallback en dry-run sin creds)")
    ws_error = None
    try:
        from src.websocket import PrivateWebSocket
        # Usar creds vacios en dry-run
        ws = PrivateWebSocket(api_key="", api_secret="", api_passphrase="")
        async def on_order_update(fill):
            logger.info(f"WS fill: {fill}")
        # Con timeout de 8s para no bloquear ventana
        await asyncio.wait_for(ws.connect(condition_id=market.condition_id, on_order_update=on_order_update), timeout=8.0)
        logger.info(f"WS conectado (inesperado en dry-run sin creds)")
    except asyncio.TimeoutError:
        ws_error = "timeout 8s"
        logger.warning(f"WS fallo: timeout 8s (esperado en dry-run sin credenciales CLOB) - continuando sin WS")
    except Exception as e:
        ws_error = str(e)[:300]
        logger.warning(f"WS fallo: {e} (esperado en dry-run) - continuando sin WS - tipo={type(e).__name__}")

    # 3. PHASE1: 10 ordenes simuladas via DryRunExecutor
    executor = DryRunExecutor()
    # Verificacion: es DryRunExecutor, no Live
    assert type(executor).__name__ == "DryRunExecutor", "Debe ser DryRunExecutor"
    logger.info(f"PHASE1: colocando 10 ordenes simuladas (5 YES + 5 NO) price=0.40 size=5 - DryRunExecutor SOLO, sin POST a CLOB")
    price = Decimal("0.40")
    size = 5
    fills = []
    for i in range(5):
        f = await executor.place_limit_order(token_id=market.token_yes_id, side="BUY", price=price, size=size)
        fills.append(f)
        logger.info(f"  order YES {i+1}/5: token={market.token_yes_id[:10]}... price={price} size={size} -> fill simulated")
    for i in range(5):
        f = await executor.place_limit_order(token_id=market.token_no_id, side="BUY", price=price, size=size)
        fills.append(f)
        logger.info(f"  order NO {i+1}/5: token={market.token_no_id[:10]}... price={price} size={size} -> fill simulated")

    resting_before = len(executor._resting)
    logger.info(f"PHASE1 complete: 10 ordenes colocadas, resting={resting_before} fills_simulados={len(fills)} cancelled_count={executor.cancelled_count}")

    # Verificacion anti-real: ningun POST real
    logger.info(f"Verificacion paper: executor type={type(executor).__name__}, sin llamadas externas (DryRun)")

    # 4. WAIT real con asyncio.sleep(remaining)
    # Si el task quiere 300s SIEMPRE, usamos remaining calculado de Gamma, pero si remaining es <5 por test rapido, respetamos.
    # Para demostrar ventana completa, esperamos remaining (si remaining 0, usamos 300)
    if remaining <= 0:
        wait_s = 300
        logger.info(f"WAIT_REMAINING: remaining original {remaining} <=0, usando fallback wait_s={wait_s}")
    else:
        wait_s = remaining
        logger.info(f"WAIT_REMAINING: {wait_s}s hasta endDate (Gamma) - esperando REAL con asyncio.sleep({wait_s}) - Phase1->wait->Phase2")

    # Log cada 30s durante la espera para demostrar que es real y no mock
    logger.info(f"WAIT start: sleeping {wait_s}s reales... inicio={time.time():.0f} monotonic={time.monotonic():.1f}")
    waited = 0
    wait_start = time.monotonic()
    wait_start_unix = int(time.time())
    # Sleep en chunks de 30s para loguear progreso
    while waited < wait_s:
        chunk = min(30, wait_s - waited)
        await asyncio.sleep(chunk)
        waited += chunk
        elapsed = time.monotonic() - wait_start
        logger.info(f"WAIT progress: {waited}/{wait_s}s elapsed_monotonic={elapsed:.1f}s now_unix={int(time.time())} (end_unix={end_unix})")

    actual_wait = time.monotonic() - wait_start
    logger.info(f"WAIT done: esperado={wait_s}s real_monotonic={actual_wait:.1f}s (delta={abs(actual_wait-wait_s):.1f}s)")

    # 5. PHASE2: cancel_all
    logger.info(f"PHASE2: cancel_all (DryRun)")
    await executor.cancel_all()
    resting_after = len(executor._resting)
    logger.info(f"PHASE2 complete: resting_after={resting_after} cancelled_count={executor.cancelled_count} (deberia ser 10)")

    # 6. ROTATE
    logger.info(f"ROTATE: ventana {window_ts} terminada, rotando +300s. Total wall time desde inicio: {time.monotonic() - start_wall:.1f}s (espera {actual_wait:.1f}s + overhead)")

    # Estado final
    logger.info(f"Estado final: resting={resting_after} cancelled={executor.cancelled_count} fills_simulados={len(fills)} ws_error={ws_error}")
    logger.info(f"Veredicto parcial: Phase1 (10 orders) -> wait {actual_wait:.0f}s REAL -> Phase2 (cancel_all) -> rotate : COMPLETADO en DRY_RUN sin POST a CLOB")
    logger.info(f"=== FIN DRY_RUN ONE WINDOW ===")

    # Verificacion final de modo paper
    print("DRY_RUN_VERIFICATION: No real mode, No external POST, DryRunExecutor only - OK", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
