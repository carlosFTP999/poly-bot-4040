"""Unit tests for src/market.py current_window_ts and discover."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.market import current_window_ts, discover, _build_market_info, _extract_markets
from src.types import MarketInfo


class TestCurrentWindowTs:
    """Test current_window_ts function."""

    def test_exact_window_boundary(self) -> None:
        """Given time 1694000447, result should be 1694000400."""
        result = current_window_ts(1694000447)
        assert result == 1694000400

    def test_midnight_aligned(self) -> None:
        """Given exact multiple of 300, result should be unchanged."""
        result = current_window_ts(1694000400)
        assert result == 1694000400

    def test_window_boundary_zero(self) -> None:
        """Given 0, result should be 0."""
        result = current_window_ts(0)
        assert result == 0

    def test_window_boundary_large(self) -> None:
        """Given a large timestamp, compute correctly."""
        result = current_window_ts(1694001234)
        assert result == 1694001000


class TestExtractMarkets:
    """Test _extract_markets helper."""

    def test_list_response(self) -> None:
        assert _extract_markets([{"id": 1}]) == [{"id": 1}]

    def test_dict_with_markets_key(self) -> None:
        response = {"markets": [{"id": 1}, {"id": 2}]}
        assert _extract_markets(response) == [{"id": 1}, {"id": 2}]

    def test_dict_single_market(self) -> None:
        response = {"id": 1, "condition_id": "abc"}
        assert _extract_markets(response) == [response]

    def test_empty_response(self) -> None:
        """Given a mock client returning empty dict, _extract_markets returns [{}]."""
        assert _extract_markets({}) == [{}]


class TestBuildMarketInfo:
    """Test _build_market_info helper."""

    def test_valid_market(self) -> None:
        market = {
            "condition_id": "cond_abc",
            "tokens": [
                {"outcome": "Yes", "token_id": "tok_yes"},
                {"outcome": "No", "token_id": "tok_no"},
            ],
            "start_date": 1694000400,
            "end_date": 1694000700,
        }
        result = _build_market_info(market)
        assert result is not None
        assert result.condition_id == "cond_abc"
        assert result.token_yes_id == "tok_yes"
        assert result.token_no_id == "tok_no"

    def test_missing_tokens(self) -> None:
        market = {"condition_id": "cond_abc", "tokens": []}
        result = _build_market_info(market)
        assert result is None

    def test_missing_condition_id(self) -> None:
        market = {
            "tokens": [
                {"outcome": "Yes", "token_id": "tok_yes"},
            ],
        }
        result = _build_market_info(market)
        assert result is None


class TestDiscover:
    """Test discover function with injectable HTTP client."""

    @pytest.mark.asyncio
    async def test_market_found(self) -> None:
        """Given a mock client returning a market JSON, discover returns MarketInfo."""
        mock_client = AsyncMock(return_value={
            "condition_id": "cond_abc",
            "tokens": [
                {"outcome": "Yes", "token_id": "tok_yes"},
                {"outcome": "No", "token_id": "tok_no"},
            ],
            "start_date": 1694000400,
            "end_date": 1694000700,
        })
        result = await discover(
            client=mock_client,
            window_ts=1694000400,
        )
        assert isinstance(result, MarketInfo)
        assert result.condition_id == "cond_abc"
        mock_client.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_market_not_found(self) -> None:
        """Given a mock client returning None, discover returns None."""
        mock_client = AsyncMock(return_value=None)
        result = await discover(
            client=mock_client,
            window_ts=1694000400,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        """Given a mock client returning empty list, discover returns None."""
        mock_client = AsyncMock(return_value=[])
        result = await discover(
            client=mock_client,
            window_ts=1694000400,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_client_error(self) -> None:
        """Given a mock client that raises, discover returns None."""
        mock_client = AsyncMock(side_effect=Exception("Connection error"))
        result = await discover(
            client=mock_client,
            window_ts=1694000400,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_mock_client_no_real_http(self) -> None:
        """Verify no real HTTP request is made; mock client is called."""
        mock_client = AsyncMock(return_value={
            "condition_id": "cond_test",
            "tokens": [
                {"outcome": "Yes", "token_id": "t_yes"},
                {"outcome": "No", "token_id": "t_no"},
            ],
            "start_date": 0, "end_date": 0,
        })
        result = await discover(
            client=mock_client,
            window_ts=1694000400,
        )
        assert result is not None
        mock_client.assert_awaited_once()
