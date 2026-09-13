"""Engine module for the BTC 5-Minute Hedge Bot.

Orchestrates the 300-second window lifecycle: discover → connect WS →
place orders (Phase 1) → wait → cancel-all (Phase 2) → rotate.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

from src.types import Fill, MarketInfo, WindowState
from src.config import Settings
from py_clob_client.clob_types import OrderArgs

logger = logging.getLogger(__name__)


class Engine:
    """Central coordinator driving the bot through each trading window.

    All dependencies are injectable callables for deterministic unit testing
    without network calls.
    """

    # Early-entry tuning: retry window when Gamma hasn't indexed the new slug yet.
    DISCOVER_RETRY_TIMEOUT_S = 5.0
    DISCOVER_RETRY_INTERVAL_S = 0.5
    # Optional pre-warm: this many seconds before window end, probe next window.
    PREWARM_BEFORE_END_S = 5.0
    # Max seconds into window to enter; otherwise skip.
    MAX_LATE_S = 15.0

    def __init__(
        self,
        executor: Any,
        discover: Callable[..., Awaitable[Optional[MarketInfo]]],
        ws_connect: Callable[..., Awaitable[None]],
        balance_check: Callable[..., Awaitable[Decimal]],
        now: Callable[[], int],
        config: Settings,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the Engine with injectable dependencies.

        Args:
            executor: Executor Protocol instance (DryRunExecutor or LiveClobExecutor).
            discover: Async callable returning Optional[MarketInfo].
            ws_connect: Async callable establishing WebSocket connection.
            balance_check: Async callable returning pUSD balance as Decimal.
            now: Callable returning current Unix epoch in seconds.
            config: Frozen Settings object.
            sleep: Injectable async sleep (defaults to asyncio.sleep).
        """
        self._executor = executor
        self._discover = discover
        self._ws_connect = ws_connect
        self._balance_check = balance_check
        self._now = now
        self._config = config
        self._sleep = sleep  # None -> asyncio.sleep resolved lazily (patch-friendly)
        self._window_state = WindowState(window_ts=0, phase="DISCOVERING")
        self._prewarmed: dict[int, MarketInfo] = {}

    async def run_cycle(self) -> None:
        """Execute one full trading window cycle.

        Lifecycle:
        1. Discover market for current window
        2. Connect WebSocket
        3. Check balance (skip if < TOTAL_CAP)
        4. Phase 1: Place 10 GTC limit orders
        5. Wait until window expiry
        6. Phase 2: Cancel all pending orders
        7. Rotate to next window
        """
        while True:
            window_ts = (self._now() // 300) * 300
            self._window_state = WindowState(
                window_ts=window_ts,
                phase="DISCOVERING",
            )

            # Step 1: Discover market (retry up to 5s if Gamma lags indexing)
            market = await self._discover_with_retry(window_ts)
            if market is None:
                logger.info("No market found for window; rotating +300s")
                await self._rotate()
                continue

            # Detect late entry: if we're more than MAX_LATE_S into the window, skip
            seconds_into_window = self._now() - window_ts
            if seconds_into_window > self.MAX_LATE_S:
                logger.warning(
                    "Entered %ds into window (max %ds); skipping. Next window at %ds",
                    seconds_into_window, self.MAX_LATE_S, window_ts + 300,
                )
                await self._rotate()
                continue

            self._window_state = WindowState(
                window_ts=window_ts,
                phase="PHASE1",
                market_info=market,
            )
            logger.info("Market discovered: %s", market.condition_id)

            # Step 2: Connect WebSocket (fail-graceful)
            try:
                await self._ws_connect(
                    condition_id=market.condition_id,
                    on_order_update=self._handle_fill,
                )
            except Exception:
                logger.warning("WebSocket unavailable; continuing without real-time fills")

            # Step 3: Balance check
            if self._config.DRY_RUN:
                logger.info("DRY_RUN: assuming sufficient balance")
            else:
                try:
                    balance = await self._balance_check()
                except Exception:
                    logger.warning("Balance check failed; rotating")
                    await self._rotate()
                    continue
                if balance is None or balance == 0 or balance < self._config.TOTAL_CAP:
                    logger.warning(
                        "Insufficient balance (%s < %s); rotating",
                        balance, self._config.TOTAL_CAP,
                    )
                    await self._rotate()
                    continue

            # Step 4: Phase 1 — Place 10 orders
            await self._phase1(market)

            # Step 5: Wait for window expiry (always full 300s)
            await self._wait_window_end()

            # Step 6: Phase 2 — Cancel all
            await self._phase2()

            # Step 7: Rotate to next window
            await self._rotate()

    async def _asleep(self, delay: float) -> None:
        """Injectable sleep; resolves asyncio.sleep lazily for patchability."""
        if self._sleep is not None:
            await self._sleep(delay)
        else:
            await asyncio.sleep(delay)

    async def _call_discover(self, window_ts: int | None = None) -> Optional[MarketInfo]:
        """Call the injected discover, passing window_ts when it accepts it.

        Keeps backward compatibility with zero-arg discover callables
        (existing tests / main.py lambda) while allowing window-aware
        discovery for pre-warm of window_ts + 300.
        """
        if window_ts is None:
            return await self._discover()
        try:
            return await self._discover(window_ts=window_ts)  # type: ignore[call-arg]
        except TypeError:
            return await self._discover()

    async def _discover_with_retry(self, window_ts: int) -> Optional[MarketInfo]:
        """Retry discover() for up to 5s at 0.5s intervals when market is None.

        Covers the case where we cross a multiple of 300 but Gamma has not
        indexed the new slug yet. Returns None only after the timeout so the
        caller can rotate +300s.
        """
        if window_ts in self._prewarmed:
            return self._prewarmed[window_ts]
        market = await self._call_discover(window_ts)
        if market is not None:
            return market
        elapsed = 0.0
        while elapsed < self.DISCOVER_RETRY_TIMEOUT_S:
            await self._asleep(self.DISCOVER_RETRY_INTERVAL_S)
            elapsed += self.DISCOVER_RETRY_INTERVAL_S
            market = await self._call_discover(window_ts)
            if market is not None:
                return market
        return None

    async def _prewarm_next_window(self, window_ts: int) -> Optional[MarketInfo]:
        """Best-effort single probe of window_ts + 300. No orders placed.

        Caches the MarketInfo so the next cycle can enter at second 0.
        Never advances Phase 1.
        """
        next_ts = window_ts + 300
        if next_ts in self._prewarmed:
            return self._prewarmed[next_ts]
        try:
            market = await self._call_discover(next_ts)
        except Exception:
            return None
        if market is not None:
            self._prewarmed[next_ts] = market
        return market

    async def _phase1(self, market: MarketInfo) -> None:
        """Place 2 GTC limit orders: 1 YES + 1 NO at PRICE_THRESHOLD (5 shares each).

        Uses batch order placement when the executor supports it
        (LiveClobExecutor, DryRunExecutor, PaperLiveExecutor all implement
        place_limit_orders_batch). Falls back to sequential placement
        for any executor that doesn't implement the batch method.

        Includes exponential backoff retry (D5) at Engine level as
        defense in depth for executors without built-in retry.

        Args:
            market: Discovered market with token IDs.
        """
        from src.executor import _retry_with_backoff

        price = self._config.PRICE_THRESHOLD
        size = self._config.SHARE_FLOOR

        # Build the 2 OrderArgs (1 YES + 1 NO), each at SHARE_FLOOR (5 shares)
        order_args_list: list[OrderArgs] = [
            OrderArgs(
                token_id=market.token_yes_id,
                price=float(price),
                size=float(size),
                side="BUY",
            ),
            OrderArgs(
                token_id=market.token_no_id,
                price=float(price),
                size=float(size),
                side="BUY",
            ),
        ]

        # Prefer batch method if available, else fall back to sequential
        # hasattr works for both real executors and AsyncMock (tests)
        if hasattr(self._executor, "place_limit_orders_batch"):
            # D5: Wrap batch call with retry as defense in depth
            await _retry_with_backoff(
                lambda: self._executor.place_limit_orders_batch(order_args_list)
            )
        else:
            # Fallback for any executor without batch support - retry each order
            for args in order_args_list:
                await _retry_with_backoff(
                    lambda a=args: self._executor.place_limit_order(
                        token_id=a.token_id,
                        side=a.side,
                        price=price,
                        size=size,
                    )
                )

        logger.info("Phase 1 complete: 2 orders placed (1 YES + 1 NO, 5 shares each)")

    async def _wait_window_end(self) -> None:
        """Wait until the current 300-second window expires.

        Always waits the full 300s regardless of fill status.
        The wait is calculated as window_end - now.
        """
        window_ts_raw = self._now()
        window_ts = (window_ts_raw // 300) * 300
        window_end = window_ts + 300
        remaining = max(0, window_end - self._now())

        logger.info("Waiting %d seconds for window expiry", remaining)
        prewarm_at = self.PREWARM_BEFORE_END_S
        if remaining > prewarm_at:
            await self._asleep(remaining - prewarm_at)
            # Best-effort pre-warm of next window; never places orders.
            await self._prewarm_next_window(window_ts)
            await self._asleep(prewarm_at)
        else:
            await self._asleep(remaining)

    async def _phase2(self) -> None:
        """Cancel all pending/unfilled GTC orders via cancel_all."""
        await self._executor.cancel_all()
        logger.info("Phase 2 complete: all orders cancelled")

    async def _rotate(self) -> None:
        """Advance the window by +300s and restart the cycle.

        The next window_ts is computed from the current time.
        """
        logger.info("Rotating to next 300s window")
        # The next discover() call in run_cycle will compute the
        # new window_ts via current_window_ts()

    async def _handle_fill(self, fill: Fill) -> None:
        """Process a fill event from WebSocket order_update.

        Records the fill in window state but does NOT shorten the wait.

        Args:
            fill: The Fill event from the WebSocket.
        """
        self._window_state = WindowState(
            window_ts=self._now(),
            phase="PHASE1",
            market_info=self._window_state.market_info,
            fills=self._window_state.fills + (fill,),
            orders_placed=self._window_state.orders_placed + 1,
        )
        logger.debug("Fill recorded: %s", fill)
