"""Unit tests for prewarm logic in Engine.

Verifies that:
1. _prewarm_next_window caches MarketInfo for window_ts + 300.
2. _discover_with_retry finds the prewarmed market INSTANTANEOUSLY at next window start.
3. _prewarm_next_window NEVER places orders (only discovers).
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call

from src.engine import Engine
from src.config import Settings
from src.types import MarketInfo


def _make_market(condition_id: str = "cond_next") -> MarketInfo:
    return MarketInfo(
        condition_id=condition_id,
        token_yes_id="tok_yes_next",
        token_no_id="tok_no_next",
        start_date=0,
        end_date=0,
    )


def _make_engine(now_val: int = 1694000400) -> tuple[Engine, MagicMock]:
    """Factory: returns (engine, mock_executor) with discover wired to track calls."""
    settings = Settings()
    mock_executor = MagicMock()
    mock_executor.place_limit_order = AsyncMock()
    mock_executor.cancel_all = AsyncMock()
    mock_discover = AsyncMock(return_value=None)
    engine = Engine(
        executor=mock_executor,
        discover=mock_discover,
        ws_connect=AsyncMock(),
        balance_check=AsyncMock(return_value=Decimal("5.00")),
        now=lambda: now_val,
        config=settings,
    )
    return engine, mock_executor, mock_discover


class TestPrewarmCachesMarketInfo:
    """_prewarm_next_window must cache MarketInfo for window_ts + 300."""

    @pytest.mark.asyncio
    async def test_prewarm_caches_next_window_market(self) -> None:
        """After _prewarm_next_window(window_ts=0), _prewarmed[300] must be set."""
        engine, _, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        mock_discover.return_value = market

        result = await engine._prewarm_next_window(window_ts=0)

        assert result is market
        assert 300 in engine._prewarmed
        assert engine._prewarmed[300] is market
        # discover was called with next_ts = 0 + 300 = 300
        mock_discover.assert_awaited_once_with(window_ts=300)

    @pytest.mark.asyncio
    async def test_prewarm_skips_discover_if_already_cached(self) -> None:
        """If _prewarmed[next_ts] exists, _call_discover must NOT be called again."""
        engine, _, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        engine._prewarmed[300] = market

        result = await engine._prewarm_next_window(window_ts=0)

        assert result is market
        mock_discover.assert_not_awaited()  # No network call — instant cache hit


class TestDiscoverWithRetryFindsPrewarmedMarket:
    """_discover_with_retry must find prewarmed market INSTANTANEOUSLY at window start."""

    @pytest.mark.asyncio
    async def test_discover_with_retry_returns_prewarmed_market(self) -> None:
        """When _prewarmed[window_ts] exists, _discover_with_retry returns it without calling discover."""
        engine, _, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        engine._prewarmed[600] = market

        result = await engine._discover_with_retry(window_ts=600)

        assert result is market
        mock_discover.assert_not_awaited()  # Zero network calls — instant cache hit

    @pytest.mark.asyncio
    async def test_discover_with_retry_calls_discover_when_not_prewarmed(self) -> None:
        """If not prewarmed, _discover_with_retry must fall through to _call_discover."""
        engine, _, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        mock_discover.return_value = market

        result = await engine._discover_with_retry(window_ts=1200)

        assert result is market
        mock_discover.assert_awaited_once_with(window_ts=1200)

    @pytest.mark.asyncio
    async def test_prewarm_at_295s_next_window_found_at_300s(self) -> None:
        """Simulate prewarm at window_ts=0 (295s into a 300s window):
        _prewarmed[300] is set. When run_cycle starts the next window at window_ts=300,
        _discover_with_retry(300) returns cached market INSTANTLY."""
        engine, _, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        mock_discover.return_value = market

        # Prewarm phase: called with window_ts=0, caches window_ts=300
        await engine._prewarm_next_window(window_ts=0)
        assert 300 in engine._prewarmed

        # New window starts at window_ts=300 — must find cached market instantly
        mock_discover.reset_mock()  # Clear call history from prewarm
        result = await engine._discover_with_retry(window_ts=300)

        assert result is market
        mock_discover.assert_not_awaited()  # INSTANT — no network, no delay


class TestPrewarmDoesNotPlaceOrders:
    """_prewarm_next_window must NEVER place orders — only discovers."""

    @pytest.mark.asyncio
    async def test_prewarm_never_calls_place_limit_order(self) -> None:
        """_prewarm_next_window must not call executor.place_limit_order."""
        engine, mock_executor, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        mock_discover.return_value = market

        await engine._prewarm_next_window(window_ts=0)

        mock_executor.place_limit_order.assert_not_awaited()
        mock_executor.cancel_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prewarm_does_not_advance_phase(self) -> None:
        """After prewarm, window state must still be in DISCOVERING, not PHASE1."""
        engine, _, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        mock_discover.return_value = market

        assert engine._window_state.phase == "DISCOVERING"
        await engine._prewarm_next_window(window_ts=0)
        assert engine._window_state.phase == "DISCOVERING"

    @pytest.mark.asyncio
    async def test_prewarm_does_not_call_phase1(self) -> None:
        """_prewarm_next_window must not call _phase1 internally."""
        engine, mock_executor, mock_discover = _make_engine(now_val=1694000400)
        market = _make_market()
        mock_discover.return_value = market

        phase1_before = mock_executor.place_limit_order.call_count
        await engine._prewarm_next_window(window_ts=0)
        phase1_after = mock_executor.place_limit_order.call_count
        assert phase1_before == phase1_after == 0
