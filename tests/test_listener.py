"""Unit tests for background listener event filtering, debouncing, mutex, and error recovery."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch
import pytest

from src.listener import (
    EXPECTED_EVENT_DATA,
    EXPECTED_EVENT_ID,
    OmenButtonListener,
    SingleInstanceMutex,
)


class MockWmiEvent:
    """Mock for WMI hpqBEvnt event object."""

    def __init__(self, event_id: int | None, event_data: int | None):
        self.EventID = event_id
        self.EventData = event_data


def test_event_filtering_valid():
    """Verify listener accepts event matching EventID=29 and EventData=8613."""
    listener = OmenButtonListener()
    valid_event = MockWmiEvent(EXPECTED_EVENT_ID, EXPECTED_EVENT_DATA)
    assert listener.is_valid_omen_event(valid_event)


@pytest.mark.parametrize(
    "event_id,event_data",
    [
        (28, 8613),     # Wrong EventID
        (29, 8614),     # Wrong EventData
        (0, 0),         # Zero values
        (None, 8613),   # None EventID
        (29, None),     # None EventData
    ],
)
def test_event_filtering_invalid(event_id, event_data):
    """Verify listener rejects events that do not match the OMEN button signature."""
    listener = OmenButtonListener()
    invalid_event = MockWmiEvent(event_id, event_data)
    assert not listener.is_valid_omen_event(invalid_event)


def test_event_filtering_malformed_object():
    """Verify listener safely rejects malformed or unexpected objects without throwing."""
    listener = OmenButtonListener()
    assert not listener.is_valid_omen_event(object())
    assert not listener.is_valid_omen_event(None)
    assert not listener.is_valid_omen_event("some string")


@patch("src.listener.launch_or_activate_app")
@patch("src.listener.load_config")
def test_debounce_suppresses_rapid_events(mock_load_config, mock_launch_app):
    """Verify that rapid successive events within the debounce window are suppressed."""
    mock_load_config.return_value = {
        "version": 1,
        "selected_app": {
            "name": "Test App",
            "launch_type": "executable",
            "target": "notepad.exe",
        },
    }
    mock_launch_app.return_value = (True, "OK")

    listener = OmenButtonListener(debounce_interval=0.3)

    # First press triggers launch
    listener.on_button_press()
    assert mock_launch_app.call_count == 1

    # Second press immediately (e.g. 50ms later) should be debounced
    listener.on_button_press()
    assert mock_launch_app.call_count == 1

    # Wait beyond debounce interval
    time.sleep(0.35)
    listener.on_button_press()
    assert mock_launch_app.call_count == 2


@patch("src.listener.launch_or_activate_app")
@patch("src.listener.load_config")
def test_listener_handles_missing_app_config(mock_load_config, mock_launch_app):
    """Verify listener handles empty selected_app gracefully without crashing."""
    mock_load_config.return_value = {"version": 1, "selected_app": None}
    listener = OmenButtonListener(debounce_interval=0.0)

    # Should not raise exception
    listener.on_button_press()
    mock_launch_app.assert_not_called()


def test_single_instance_mutex():
    """Verify single instance mutex prevents concurrent duplicate instances."""
    mutex_name = "Local\\TestOmenUniqueMutex_123"
    m1 = SingleInstanceMutex(mutex_name)
    m2 = SingleInstanceMutex(mutex_name)

    try:
        assert m1.acquire() is True
        # Second instance must fail to acquire
        assert m2.acquire() is False
    finally:
        m1.release()
        m2.release()

    # After releasing, acquiring again must succeed
    m3 = SingleInstanceMutex(mutex_name)
    try:
        assert m3.acquire() is True
    finally:
        m3.release()


@patch("time.sleep")
@patch("win32com.client.GetObject")
def test_wmi_recovery_behavior(mock_get_object, mock_sleep):
    """Verify listener recovers from initial WMI subscription failure and retries."""
    listener = OmenButtonListener()
    # Mock stop after 2 iterations
    attempts = [0]

    def fake_get_object(ns):
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("Simulated WMI connection failure")
        # Second attempt signals stop
        listener.stop()
        mock_sink = MagicMock()
        mock_sink.NextEvent.side_effect = RuntimeError("Stop loop")
        return MagicMock(ExecNotificationQuery=MagicMock(return_value=mock_sink))

    mock_get_object.side_effect = fake_get_object

    # Avoid acquiring actual mutex in unit test
    with patch.object(listener._mutex, "acquire", return_value=True), \
         patch.object(listener._mutex, "release"):
        listener.run()

    # Verify that it retried after the failure
    assert attempts[0] >= 2
    mock_sleep.assert_called_with(5)
