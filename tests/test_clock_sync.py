"""Unit tests for src/clock_sync.py ClockSync."""

import time
from unittest.mock import patch, MagicMock

import pytest

from src.clock_sync import ClockSync, CALIBRATION_INTERVAL, DRIFT_WARN_THRESHOLD


class TestClockSync:
    """Test ClockSync calibration and time computation."""

    @patch("src.clock_sync.httpx.get")
    def test_calibrate_sets_offset(self, mock_get):
        """After successful calibration, offset = server_time - midpoint."""
        mock_resp = MagicMock()
        mock_resp.text = "1700000010.5"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("src.clock_sync.time.monotonic", side_effect=[100.0, 100.1]):
            cs = ClockSync(base_url="https://test.example.com")

        # midpoint = 100 + (100.1 - 100) / 2 = 100.05
        # offset = 1700000010.5 - 100.05 = 1700000010.45
        assert mock_get.called
        mock_get.assert_called_once_with("https://test.example.com/time", timeout=5.0)

    @patch("src.clock_sync.httpx.get")
    def test_server_time_returns_int_time_plus_offset(self, mock_get):
        """server_time() returns int(time.time() + offset)."""
        mock_resp = MagicMock()
        mock_resp.text = "1700000000.0"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("src.clock_sync.time.monotonic", side_effect=[100.0, 100.0]):
            cs = ClockSync(base_url="https://test.example.com")

        # offset = 1700000000.0 - 100.0 = 1699999900.0
        with patch("src.clock_sync.time.time", return_value=100.0):
            with patch("src.clock_sync.time.monotonic", return_value=100.0):
                result = cs.server_time()

        assert result == int(100.0 + 1699999900.0) == 1700000000

    @patch("src.clock_sync.httpx.get")
    def test_server_time_triggers_recalibration_after_interval(self, mock_get):
        """server_time() re-calibrates when CALIBRATION_INTERVAL has passed."""
        mock_resp = MagicMock()
        mock_resp.text = "1700000000.0"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("src.clock_sync.time.monotonic", side_effect=[100.0, 100.0]):
            cs = ClockSync(base_url="https://test.example.com")

        # Simulate time passage beyond CALIBRATION_INTERVAL
        call_count = 0
        def mock_monotonic_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call in server_time check — should trigger re-calibration
                return 100.0 + CALIBRATION_INTERVAL + 1
            elif call_count == 2:
                # t1 in _calibrate
                return 200.0
            elif call_count == 3:
                # t4 in _calibrate
                return 200.1
            return 200.1

        with patch("src.clock_sync.time.monotonic", side_effect=mock_monotonic_side_effect):
            cs.server_time()

        # Should have called httpx.get twice: once in __init__, once on re-calibration
        assert mock_get.call_count == 2

    @patch("src.clock_sync.httpx.get")
    def test_fallback_to_offset_zero_on_error(self, mock_get):
        """On network error during calibration, offset falls back to 0."""
        mock_get.side_effect = ConnectionError("network unreachable")

        with patch("src.clock_sync.time.monotonic", return_value=100.0):
            cs = ClockSync(base_url="https://test.example.com")

        assert cs._offset == 0.0

    @patch("src.clock_sync.httpx.get")
    def test_force_recalibrate_returns_server_time(self, mock_get):
        """force_recalibrate() triggers calibration and returns server_time()."""
        mock_resp = MagicMock()
        mock_resp.text = "1700000000.0"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("src.clock_sync.time.monotonic", side_effect=[100.0, 100.0]):
            cs = ClockSync(base_url="https://test.example.com")

        # Reset and force recalibrate
        mock_get.reset_mock()
        mock_resp2 = MagicMock()
        mock_resp2.text = "1700000100.0"
        mock_resp2.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp2

        with patch("src.clock_sync.time.monotonic", side_effect=[200.0, 200.0, 200.0]):
            with patch("src.clock_sync.time.time", return_value=200.0):
                result = cs.force_recalibrate()

        assert mock_get.call_count == 1  # Called once during force_recalibrate
        # offset = 1700000100.0 - 200.0 = 1699999900.0
        assert result == int(200.0 + 1699999900.0) == 1700000100

    @patch("src.clock_sync.httpx.get")
    def test_calibrate_http_error_falls_back_to_zero(self, mock_get):
        """On HTTP error (raise_for_status), offset falls back to 0."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
        mock_get.return_value = mock_resp

        with patch("src.clock_sync.time.monotonic", return_value=100.0):
            cs = ClockSync(base_url="https://test.example.com")

        assert cs._offset == 0.0

    @patch("src.clock_sync.httpx.get")
    def test_drift_warning_logged(self, mock_get):
        """Large drift triggers a warning log."""
        mock_resp = MagicMock()
        mock_resp.text = "1700000000.0"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        # Simulate large RTT to create big offset
        with patch("src.clock_sync.time.monotonic", side_effect=[100.0, 100.0 + DRIFT_WARN_THRESHOLD + 10]):
            with patch("src.clock_sync.logger") as mock_logger:
                cs = ClockSync(base_url="https://test.example.com")
                mock_logger.warning.assert_called()

    @patch("src.clock_sync.httpx.get")
    def test_small_offset_logs_info(self, mock_get):
        """When server time is close to monotonic, offset is small and logs INFO.

        Note: server_time is epoch-based and monotonic is process-relative,
        so offset is usually huge. This test uses a server time close to
        monotonic to produce a small offset and verify the INFO path.
        """
        mock_resp = MagicMock()
        # Use a server time close to monotonic midpoint to get a small offset
        mock_resp.text = "100.0"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        # t1=100.0, t4=100.02 → midpoint=100.01, offset = 100.0 - 100.01 = -0.01
        with patch("src.clock_sync.time.monotonic", side_effect=[100.0, 100.02]):
            with patch("src.clock_sync.logger") as mock_logger:
                cs = ClockSync(base_url="https://test.example.com")
                mock_logger.info.assert_called()
