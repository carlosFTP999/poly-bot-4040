"""Entry point for the BTC 5-Minute Hedge Bot.

Loads configuration, selects the executor based on mode, and starts
the engine loop for continuous window-based trading.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
import urllib.request
import json
from decimal import Decimal
from typing import Any, Awaitable, Callable

from src.config import Settings, load_settings_from_env
from src.executor import DryRunExecutor, LiveClobExecutor
from src.engine import Engine
from src.market import current_window_ts, discover
from src.websocket import PrivateWebSocket

logger = logging.getLogger(__name__)


def select_executor(settings: Settings) -> Any:
    """Select the appropriate executor based on configuration mode.

    DRY_RUN=True → DryRunExecutor (paper trading)
    LIVE_ENABLED=True → LiveClobExecutor (live trading)

    Args:
        settings: Frozen Settings object with mode selection.

    Returns:
        An Executor instance (DryRunExecutor or LiveClobExecutor).

    Raises:
        ValueError: If settings are invalid.
    """
    if settings.DRY_RUN and not settings.LIVE_ENABLED:
        logger.info("Selected DryRunExecutor (paper trading mode)")
        return DryRunExecutor()

    if settings.LIVE_ENABLED and not settings.DRY_RUN:
        logger.info("Selected LiveClobExecutor (live trading mode)")
        return LiveClobExecutor(
            api_key=settings.POLYMARKET_API_KEY,
            api_secret=settings.POLYMARKET_API_SECRET,
            api_passphrase=settings.POLYMARKET_API_PASSPHRASE,
            private_key=settings.POLYMARKET_PRIVATE_KEY,
            base_url=settings.CLOB_BASE_URL,
            signature_type=settings.SIGNATURE_TYPE,
        )

    raise ValueError("Invalid mode selection: exactly one of DRY_RUN/LIVE_ENABLED must be True")


async def connect_websocket(
    condition_id: str,
    on_order_update: Callable[[Any], Awaitable[None]],
    settings: Settings,
) -> None:
    """Establish a WebSocket connection to the discovered market.

    Args:
        condition_id: Market condition_id to subscribe to.
        on_order_update: Async callback for fill events.
        settings: Configuration for WebSocket credentials.
    """
    if settings.DRY_RUN:
        logger.warning("DRY_RUN mode: skipping WebSocket connection")
        return None

    ws = PrivateWebSocket(
        api_key=settings.POLYMARKET_API_KEY,
        api_secret=settings.POLYMARKET_API_SECRET,
        api_passphrase=settings.POLYMARKET_API_PASSPHRASE,
    )

    for attempt in range(1, 4):
        try:
            await ws.connect(condition_id, on_order_update)
            return
        except Exception as e:
            logger.warning(
                "WebSocket connection attempt %d failed: %s", attempt, e,
            )
            if attempt < 3:
                await asyncio.sleep(2 ** attempt)

    logger.warning("WebSocket connection failed after 3 attempts")


async def balance_check() -> Decimal:
    """Check pUSD balance via GET /balance-allowance.

    Uses asset_type=pUSD and signature_type=2.

    Returns:
        Available pUSD balance as Decimal.
    """
    try:
        from py_clob_client.clob import ClobApi

        settings = load_settings_from_env()
        api = ClobApi(host=settings.CLOB_BASE_URL, key=settings.POLYMARKET_API_KEY)
        response = api.get_balance_allowance(
            asset_type="pUSD",
            signature_type=settings.SIGNATURE_TYPE,
        )
        return Decimal(str(response.get("balance", "0")))
    except ImportError:
        logger.warning("py_clob_client not available; defaulting balance to 0")
        return Decimal("0")


async def run_bot() -> None:
    """Main bot entry point: load config, select executor, start engine loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    settings = load_settings_from_env()
    logger.info("Bot starting with mode: %s", "LIVE" if settings.LIVE_ENABLED else "DRY_RUN")

    executor = select_executor(settings)

    async def _http_get(url: str) -> Any:
        """Async HTTP GET using urllib (no extra deps)."""
        req = urllib.request.Request(url, headers={"User-Agent": "poly-bot-4040/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    async def _discover(window_ts: int) -> Any:
        return await discover(
            client=_http_get,
            window_ts=window_ts,
            gamma_base_url=settings.GAMMA_BASE_URL,
        )

    async def _ws_connect(condition_id: str, on_order_update: Any) -> None:
        await connect_websocket(condition_id, on_order_update, settings)

    engine = Engine(
        executor=executor,
        discover=_discover,
        ws_connect=_ws_connect,
        balance_check=balance_check,
        now=lambda: int(_time.time()),
        config=settings,
    )

    logger.info("Engine started. Beginning window cycles...")
    await engine.run_cycle()


if __name__ == "__main__":
    asyncio.run(run_bot())
