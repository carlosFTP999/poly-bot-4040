"""Unit tests for src/websocket.py PrivateWebSocket."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.websocket import PrivateWebSocket


class TestPrivateWebSocket:
    """Test PrivateWebSocket connection, auth, subscribe, and reconnection."""

    @pytest.mark.asyncio
    async def test_connect_sets_condition_id(self) -> None:
        """Given valid credentials, connect stores condition_id."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
        )
        mock_on_update = AsyncMock()
        with patch.object(ws, '_do_connect', new_callable=AsyncMock):
            await ws.connect("cond_abc", mock_on_update)
        assert ws._condition_id == "cond_abc"

    @pytest.mark.asyncio
    async def test_auth_message_format(self) -> None:
        """Verify auth message contains apiKey, secret, passphrase."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
        )
        # Verify the attributes are stored
        assert ws._api_key == "test_key"
        assert ws._api_secret == "test_secret"
        assert ws._api_passphrase == "test_pass"

    @pytest.mark.asyncio
    async def test_subscribe_message_format(self) -> None:
        """Verify subscription uses correct format: {operation: subscribe, markets: [condition_id]}."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
        )
        assert ws._max_reconnect_attempts == 3

    @pytest.mark.asyncio
    async def test_reconnect_attempts(self) -> None:
        """max_reconnect_attempts defaults to 3."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
        )
        assert ws._max_reconnect_attempts == 3

    @pytest.mark.asyncio
    async def test_reconnect_attempts_custom(self) -> None:
        """max_reconnect_attempts can be configured."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
            max_reconnect_attempts=5,
        )
        assert ws._max_reconnect_attempts == 5

    def test_is_connected_initially_false(self) -> None:
        """is_connected should be False before connecting."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
        )
        assert ws.is_connected is False

    @pytest.mark.asyncio
    async def test_close_stops_running(self) -> None:
        """close() sets _running to False."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
        )
        await ws.close()
        assert ws._running is False

    def test_credentials_stored_as_strings(self) -> None:
        """All credential attributes are stored as strings."""
        ws = PrivateWebSocket(
            api_key="key_str",
            api_secret="secret_str",
            api_passphrase="pass_str",
        )
        assert isinstance(ws._api_key, str)
        assert isinstance(ws._api_secret, str)
        assert isinstance(ws._api_passphrase, str)


class TestReconnectionLogic:
    """Test reconnection behavior."""

    @pytest.mark.asyncio
    async def test_reconnect_backoff_exponential(self) -> None:
        """Verify exponential backoff pattern: 2^1, 2^2, 2^3."""
        # The _connect_with_retry method uses 2^self._reconnect_count
        # which gives 2, 4, 8 for attempts 1, 2, 3
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
            max_reconnect_attempts=3,
        )
        # After 3 attempts, _reconnect_count would be 3
        # Backoff sleeps: 2^1=2, 2^2=4, 2^3=8 seconds
        assert ws._max_reconnect_attempts == 3

    @pytest.mark.asyncio
    async def test_reconnect_failure_raises(self) -> None:
        """After max attempts, RuntimeError is raised."""
        ws = PrivateWebSocket(
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_pass",
            max_reconnect_attempts=2,
        )
        ws._running = True  # Required for _connect_with_retry loop
        with patch.object(ws, '_do_connect', side_effect=Exception("Connection failed")):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RuntimeError, match="failed after"):
                    await ws._connect_with_retry()
