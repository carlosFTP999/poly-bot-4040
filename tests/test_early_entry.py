"""Early-entry tests: discover retry + prewarm. No network."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.engine import Engine
from src.config import Settings
from src.types import MarketInfo


def _mk_engine(discover, sleep=None, now=lambda: 1694000400):
    return Engine(
        executor=AsyncMock(),
        discover=discover,
        ws_connect=AsyncMock(),
        balance_check=AsyncMock(return_value=Decimal("5.00")),
        now=now,
        config=Settings(),
        sleep=sleep or AsyncMock(),
    )


def _market(cid="cond_x"):
    return MarketInfo(condition_id=cid, token_yes_id="y", token_no_id="n",
                      start_date=0, end_date=0)


class TestDiscoverRetry:
    @pytest.mark.asyncio
    async def test_absent_then_present(self):
        m = _market()
        disc = AsyncMock(side_effect=[None, None, m])
        sleeps = []

        async def sleep(s):
            sleeps.append(s)

        eng = _mk_engine(disc, sleep=sleep)
        out = await eng._discover_with_retry(1694000400)
        assert out == m
        assert disc.await_count == 3
        assert sleeps == [0.5, 0.5]

    @pytest.mark.asyncio
    async def test_absent_after_timeout(self):
        disc = AsyncMock(return_value=None)
        sleeps = []

        async def sleep(s):
            sleeps.append(s)

        eng = _mk_engine(disc, sleep=sleep)
        out = await eng._discover_with_retry(1694000400)
        assert out is None
        # 1 initial + 10 retries (5s / 0.5s)
        assert disc.await_count == 11
        assert len(sleeps) == 10

    @pytest.mark.asyncio
    async def test_zero_arg_discover_compat(self):
        m = _market()
        disc = AsyncMock(return_value=m)
        # zero-arg callable raising TypeError when given kwargs
        async def legacy():
            return m
        eng = _mk_engine(legacy, sleep=AsyncMock())
        out = await eng._discover_with_retry(1694000400)
        assert out == m


class TestPrewarm:
    @pytest.mark.asyncio
    async def test_prewarm_no_orders(self):
        m = _market()
        disc = AsyncMock(return_value=m)
        eng = _mk_engine(disc, sleep=AsyncMock())
        out = await eng._prewarm_next_window(1694000400)
        assert out == m
        eng._executor.place_limit_order.assert_not_awaited()
        eng._executor.cancel_all.assert_not_awaited()
        # cached for next cycle
        out2 = await eng._discover_with_retry(1694000700)
        assert out2 == m
        assert disc.await_count == 1  # cache hit, no extra discover

    @pytest.mark.asyncio
    async def test_prewarm_none_not_cached(self):
        disc = AsyncMock(return_value=None)
        eng = _mk_engine(disc, sleep=AsyncMock())
        assert await eng._prewarm_next_window(1694000400) is None
        assert 1694000700 not in eng._prewarmed

    @pytest.mark.asyncio
    async def test_wait_triggers_prewarm(self):
        m = _market()
        now_vals = [1694000400, 1694000400]  # window_ts, then now for remaining

        async def disc(window_ts=None):
            return m

        sleeps = []

        async def sleep(s):
            sleeps.append(s)

        eng = _mk_engine(AsyncMock(side_effect=disc), sleep=sleep,
                         now=lambda: now_vals.pop(0) if now_vals else 1694000400)
        with pytest.MonkeyPatch.context() as mp:
            pass
        # remaining = 300 -> sleeps [295, 5] and prewarm called
        eng._now = lambda: 1694000400
        await eng._wait_window_end()
        assert sleeps == [295.0, 5.0]
        assert 1694000700 in eng._prewarmed
        eng._executor.place_limit_order.assert_not_awaited()
