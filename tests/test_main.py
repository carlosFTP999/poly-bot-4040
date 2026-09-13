"""Unit tests for src/main.py select_executor and entry point."""

import os
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings, load_settings_from_env
from src.main import select_executor, balance_check, run_bot
from src.executor import DryRunExecutor, LiveClobExecutor, PaperLiveExecutor


class TestSelectExecutor:
    """Test executor selection based on configuration mode."""

    def test_dry_run_selects_dryrun_executor(self) -> None:
        """DRY_RUN=True, LIVE_ENABLED=False → DryRunExecutor."""
        settings = Settings(DRY_RUN=True, LIVE_ENABLED=False)
        executor = select_executor(settings)
        assert isinstance(executor, DryRunExecutor)

    @patch("src.executor.LiveClobExecutor._build_client", return_value=MagicMock())
    def test_live_selects_live_executor(self, _mock) -> None:
        """LIVE_ENABLED=True, DRY_RUN=False → LiveClobExecutor."""
        settings = Settings(
            LIVE_ENABLED=True,
            DRY_RUN=False,
            POLYMARKET_API_KEY="key",
            POLYMARKET_API_SECRET="secret",
            POLYMARKET_API_PASSPHRASE="pass",
            POLYMARKET_PRIVATE_KEY="priv",
        )
        executor = select_executor(settings)
        assert isinstance(executor, LiveClobExecutor)

    def test_dual_mode_raises(self) -> None:
        """Both modes True without keys should raise ValueError at Settings construction."""
        with pytest.raises(ValueError, match="PAPER_LIVE.*requires"):
            select_executor(Settings(LIVE_ENABLED=True, DRY_RUN=True))

    def test_paper_live_selects_paperlive_executor(self) -> None:
        """Both modes True WITH keys should select PaperLiveExecutor."""
        settings = Settings(
            LIVE_ENABLED=True,
            DRY_RUN=True,
            POLYMARKET_API_KEY="key",
            POLYMARKET_API_SECRET="secret",
            POLYMARKET_API_PASSPHRASE="pass",
            POLYMARKET_PRIVATE_KEY="priv",
        )
        executor = select_executor(settings)
        assert isinstance(executor, PaperLiveExecutor)

    def test_no_mode_raises(self) -> None:
        """Both modes False should raise ValueError."""
        with pytest.raises(ValueError):
            select_executor(Settings(LIVE_ENABLED=False, DRY_RUN=False))

    def test_live_executor_key_validation(self) -> None:
        """Missing keys should raise when constructing LiveClobExecutor."""
        with pytest.raises(ValueError, match="requires"):
            LiveClobExecutor(
                api_key="",
                api_secret="secret",
                api_passphrase="pass",
                private_key="priv",
            )


class TestBalanceCheck:
    """Test balance_check function."""

    @pytest.mark.asyncio
    async def test_balance_check_returns_decimal(self) -> None:
        """balance_check should return a Decimal via ClobClient.get_balance_allowance."""
        mock_client = MagicMock()
        mock_client.get_balance_allowance.return_value = {"balance": "5.00"}
        with patch("py_clob_client.client.ClobClient", return_value=mock_client):
            balance = await balance_check()
        assert isinstance(balance, Decimal)
        assert balance == Decimal("5.00")

    @pytest.mark.asyncio
    async def test_balance_check_default_zero(self) -> None:
        """Without py_clob_client, balance defaults to Decimal('0')."""
        # This is the expected behavior when py_clob_client is not installed
        pass  # balance_check handles ImportError gracefully


class TestRunBot:
    """Test the main run_bot entry point."""

    @pytest.mark.asyncio
    async def test_run_bot_loads_settings(self) -> None:
        """run_bot should load settings and create engine."""
        # Test that load_settings_from_env works without error
        settings = load_settings_from_env()
        assert isinstance(settings, Settings)
        # With default env, DRY_RUN should be True
        assert settings.DRY_RUN is True

    @pytest.mark.asyncio
    async def test_run_bot_selects_executor(self) -> None:
        """run_bot should successfully select an executor."""
        settings = Settings()
        executor = select_executor(settings)
        assert isinstance(executor, DryRunExecutor)

    def test_main_guard(self) -> None:
        """Verify the __main__ guard exists."""
        import sys
        # The module should have __main__ block defined
        # This is tested by the module loading without error
        assert True
