"""Unit tests for compatibility checking and event classification."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from src.compatibility import (
    STANDARD_EVENT_DATA,
    STANDARD_EVENT_ID,
    check_hp_conflicts,
    check_wmi_hp_driver,
    classify_event,
    get_hardware_info,
    listen_for_single_wmi_event,
)


def test_classify_event_standard():
    is_compat, msg = classify_event(STANDARD_EVENT_ID, STANDARD_EVENT_DATA)
    assert is_compat is True
    assert "100% compatible" in msg


def test_classify_event_partial():
    is_compat, msg = classify_event(STANDARD_EVENT_ID, 9999)
    assert is_compat is True
    assert "Compatible" in msg


def test_classify_event_non_standard():
    is_compat, msg = classify_event(10, 100)
    assert is_compat is False
    assert "non-standard" in msg


def test_classify_event_none():
    is_compat, msg = classify_event(None, None)
    assert is_compat is False


def test_get_hardware_info():
    info = get_hardware_info()
    assert "manufacturer" in info
    assert "model" in info
    assert "os" in info
    assert "is_hp" in info
    assert isinstance(info["is_hp"], bool)


def test_check_hp_conflicts_detected():
    mock_p1 = MagicMock()
    mock_p1.info = {"name": "OMENGamingHub.exe", "pid": 1234}

    mock_p2 = MagicMock()
    mock_p2.info = {"name": "notepad.exe", "pid": 5678}

    mock_p3 = MagicMock()
    mock_p3.info = {"name": "HPSysEvent.exe", "pid": 9101}

    with patch("psutil.process_iter", return_value=[mock_p1, mock_p2, mock_p3]):
        conflicts = check_hp_conflicts()
        assert len(conflicts) == 2
        procs = [c["process"] for c in conflicts]
        assert "omengaminghub.exe" in procs
        assert "hpsysevent.exe" in procs


def test_check_hp_conflicts_none():
    mock_p1 = MagicMock()
    mock_p1.info = {"name": "explorer.exe", "pid": 100}

    with patch("psutil.process_iter", return_value=[mock_p1]):
        conflicts = check_hp_conflicts()
        assert len(conflicts) == 0


def test_check_wmi_hp_driver_success():
    mock_cls = MagicMock()
    mock_cls.Path_.Class = "hpqBEvnt"
    mock_wmi = MagicMock()
    mock_wmi.Get.return_value = mock_cls

    with patch("win32com.client.GetObject", return_value=mock_wmi):
        ok, msg = check_wmi_hp_driver()
        assert ok is True
        assert "hpqBEvnt" in msg


def test_check_wmi_hp_driver_failure():
    with patch("win32com.client.GetObject", side_effect=Exception("Namespace not found")):
        ok, msg = check_wmi_hp_driver()
        assert ok is False
        assert "not found" in msg


def test_listen_for_single_wmi_event_timeout():
    mock_wmi = MagicMock()
    mock_events = MagicMock()
    mock_events.NextEvent.side_effect = Exception("Call timed out")
    mock_wmi.ExecNotificationQuery.return_value = mock_events

    stop_event = threading.Event()
    # Trigger stop immediately
    stop_event.set()

    with patch("win32com.client.GetObject", return_value=mock_wmi):
        eid, edata, msg = listen_for_single_wmi_event(stop_event, timeout_seconds=0.1)
        assert eid is None
        assert edata is None


def test_compatibility_dialog_instantiation():
    import sys
    from PySide6.QtWidgets import QApplication
    from src.configurator import CompatibilityDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = CompatibilityDialog()
    assert dlg.windowTitle() == "Hardware & OMEN Key Diagnostics"
    assert dlg.lbl_model.text() != ""
    assert dlg.btn_test.isEnabled() is True

