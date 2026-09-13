"""Unit tests for src/config.py Settings and load_settings_from_env."""

import os
import pytest
from decimal import Decimal

from src.config import Settings, load_settings_from_env, _parse_bool


class TestParseBool:
    """Test the _parse_bool helper."""

    def test_parse_true_values(self) -> None:
        assert _parse_bool("true") is True
        assert _parse_bool("True") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True

    def test_parse_false_values(self) -> None:
        assert _parse_bool("false") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False

    def test_parse_none(self) -> None:
        assert _parse_bool(None) is False


class TestSettingsDefaults:
    """Test default Settings values."""

    def test_default_dry_run(self) -> None:
        s = Settings()
        assert s.DRY_RUN is True
        assert s.LIVE_ENABLED is False

    def test_decimal_precision(self) -> None:
        s = Settings()
        assert s.PRICE_THRESHOLD == Decimal("0.40")
        assert s.MAX_PER_SIDE == Decimal("2.00")
        assert s.TOTAL_CAP == Decimal("4.00")
        assert not isinstance(s.PRICE_THRESHOLD, float)

    def test_share_floor_int(self) -> None:
        s = Settings()
        assert s.SHARE_FLOOR == 5
        assert isinstance(s.SHARE_FLOOR, int)

    def test_signature_type_default(self) -> None:
        s = Settings()
        assert s.SIGNATURE_TYPE == 2
        assert isinstance(s.SIGNATURE_TYPE, int)


class TestSettingsImmutability:
    """Test that Settings is frozen (immutable)."""

    def test_cannot_mutate_price_threshold(self) -> None:
        s = Settings()
        with pytest.raises(Exception):
            s.PRICE_THRESHOLD = Decimal("0.50")  # type: ignore

    def test_cannot_mutate_mode(self) -> None:
        s = Settings()
        with pytest.raises(Exception):
            s.DRY_RUN = False  # type: ignore


class TestSettingsModeValidation:
    """Test that invalid mode combinations raise errors."""

    def test_paper_live_requires_keys(self) -> None:
        """PAPER_LIVE (both True) without keys should raise."""
        with pytest.raises(ValueError, match="PAPER_LIVE.*requires"):
            Settings(LIVE_ENABLED=True, DRY_RUN=True)

    def test_no_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="must be True"):
            Settings(LIVE_ENABLED=False, DRY_RUN=False)

    def test_live_missing_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="requires"):
            Settings(LIVE_ENABLED=True, DRY_RUN=False)

    def test_paper_live_with_keys_valid(self) -> None:
        """PAPER_LIVE with all keys should be valid."""
        s = Settings(
            LIVE_ENABLED=True,
            DRY_RUN=True,
            POLYMARKET_API_KEY="key",
            POLYMARKET_API_SECRET="secret",
            POLYMARKET_API_PASSPHRASE="pass",
            POLYMARKET_PRIVATE_KEY="priv",
        )
        assert s.LIVE_ENABLED is True
        assert s.DRY_RUN is True


class TestSettingsEnvLoading:
    """Test load_settings_from_env with various env configurations."""

    def test_default_env(self, monkeypatch) -> None:
        """With no env vars, defaults to dry-run mode."""
        monkeypatch.delenv("LIVE_ENABLED", raising=False)
        monkeypatch.delenv("DRY_RUN", raising=False)
        s = load_settings_from_env()
        assert s.DRY_RUN is True
        assert s.LIVE_ENABLED is False

    def test_live_env(self, monkeypatch) -> None:
        """With live env vars, constructs LiveClobExecutor-ready Settings (DRY_RUN=false)."""
        monkeypatch.setenv("LIVE_ENABLED", "true")
        monkeypatch.setenv("DRY_RUN", "false")
        monkeypatch.setenv("POLYMARKET_API_KEY", "test_key")
        monkeypatch.setenv("POLYMARKET_API_SECRET", "test_secret")
        monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "test_pass")
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "test_priv")
        s = load_settings_from_env()
        assert s.LIVE_ENABLED is True
        assert s.DRY_RUN is False
        assert s.POLYMARKET_API_KEY == "test_key"

    def test_paper_live_env(self, monkeypatch) -> None:
        """With both LIVE_ENABLED and DRY_RUN true, constructs PAPER_LIVE Settings."""
        monkeypatch.setenv("LIVE_ENABLED", "true")
        monkeypatch.setenv("DRY_RUN", "true")
        monkeypatch.setenv("POLYMARKET_API_KEY", "test_key")
        monkeypatch.setenv("POLYMARKET_API_SECRET", "test_secret")
        monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "test_pass")
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "test_priv")
        s = load_settings_from_env()
        assert s.LIVE_ENABLED is True
        assert s.DRY_RUN is True
        assert s.POLYMARKET_API_KEY == "test_key"

    def test_signature_type_default(self, monkeypatch) -> None:
        """When SIGNATURE_TYPE not set, defaults to 2."""
        monkeypatch.delenv("SIGNATURE_TYPE", raising=False)
        s = load_settings_from_env()
        assert s.SIGNATURE_TYPE == 2


class TestConfigValidation:
    """Test config validation edge cases."""

    def test_live_mode_with_all_keys(self, monkeypatch) -> None:
        """LIVE_ENABLED with all keys should not raise."""
        monkeypatch.setenv("LIVE_ENABLED", "true")
        monkeypatch.setenv("POLYMARKET_API_KEY", "key")
        monkeypatch.setenv("POLYMARKET_API_SECRET", "secret")
        monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "pass")
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "priv")
        s = load_settings_from_env()
        assert s.LIVE_ENABLED is True

    def test_custom_env_urls(self, monkeypatch) -> None:
        """Custom GAMMA_BASE_URL and CLOB_BASE_URL are loaded."""
        monkeypatch.setenv("GAMMA_BASE_URL", "https://custom.gamma.com")
        monkeypatch.setenv("CLOB_BASE_URL", "https://custom.clob.com")
        s = load_settings_from_env()
        assert s.GAMMA_BASE_URL == "https://custom.gamma.com"
        assert s.CLOB_BASE_URL == "https://custom.clob.com"
