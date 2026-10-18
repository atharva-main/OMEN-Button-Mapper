"""Unit tests for listener lifecycle functions and Task Scheduler status queries."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.configurator import (
    check_is_listener_running,
    find_listener_executable,
    get_scheduled_task_state,
)


def test_find_listener_executable():
    """Verify find_listener_executable returns a valid path to script or binary."""
    path = find_listener_executable()
    assert path is not None
    assert isinstance(path, Path)
    assert path.exists()


def test_check_is_listener_running():
    """Verify check_is_listener_running executes without throwing exceptions."""
    is_running = check_is_listener_running()
    assert isinstance(is_running, bool)


@patch("src.configurator.subprocess.run")
def test_get_scheduled_task_state_not_found(mock_run):
    """Verify get_scheduled_task_state returns (False, False, 'Not Installed') when task is missing."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Not found")

    with patch("win32com.client.Dispatch", side_effect=Exception("COM unavailable")):
        exists, enabled, state = get_scheduled_task_state()
        assert exists is False
        assert enabled is False
        assert state == "Not Installed"


@patch("src.configurator.subprocess.run")
def test_get_scheduled_task_state_ready(mock_run):
    """Verify get_scheduled_task_state handles ready task from PowerShell output."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"TaskName": "OmenButtonRemapperListener", "State": 3}',
        stderr="",
    )

    with patch("win32com.client.Dispatch", side_effect=Exception("COM unavailable")):
        exists, enabled, state = get_scheduled_task_state()
        assert exists is True
        assert enabled is True
        assert state == "Ready"
