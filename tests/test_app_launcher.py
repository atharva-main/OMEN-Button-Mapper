"""Unit tests for application launching and target validation."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
import pytest

from src.app_launcher import (
    launch_app,
    resolve_executable,
    validate_target,
)


def test_resolve_executable_notepad():
    """Verify standard Windows executable 'notepad.exe' resolves to an existing file."""
    path = resolve_executable("notepad.exe")
    assert path is not None
    assert os.path.isfile(path)
    assert path.lower().endswith("notepad.exe")


def test_resolve_executable_nonexistent():
    """Verify non-existent executable returns None."""
    assert resolve_executable("definitely_nonexistent_executable_12345.exe") is None
    assert resolve_executable("") is None


def test_validate_target_valid_executable():
    """Validate valid executable dictionary."""
    app_dict = {
        "name": "Notepad",
        "launch_type": "executable",
        "target": "notepad.exe",
    }
    is_valid, msg = validate_target(app_dict)
    assert is_valid, msg


def test_validate_target_invalid_executable():
    """Validate invalid executable target is rejected."""
    app_dict = {
        "name": "Ghost",
        "launch_type": "executable",
        "target": "C:\\DoesNotExist\\fake.exe",
    }
    is_valid, msg = validate_target(app_dict)
    assert not is_valid
    assert "cannot resolve" in msg.lower()


def test_validate_target_packaged_app():
    """Validate packaged app AUMID format validation."""
    valid_pkg = {
        "name": "Calculator",
        "launch_type": "packaged",
        "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
    }
    is_valid, msg = validate_target(valid_pkg)
    assert is_valid, msg

    invalid_pkg = {
        "name": "Bad App",
        "launch_type": "packaged",
        "target": "MissingExclamationMark",
    }
    is_valid, msg = validate_target(invalid_pkg)
    assert not is_valid
    assert "invalid aumid" in msg.lower()


def test_validate_target_invalid_working_directory(tmp_path):
    """Validate invalid working directory is rejected."""
    app_dict = {
        "name": "Notepad",
        "launch_type": "executable",
        "target": "notepad.exe",
        "working_directory": str(tmp_path / "nonexistent_dir"),
    }
    is_valid, msg = validate_target(app_dict)
    assert not is_valid
    assert "working directory does not exist" in msg.lower()


def test_launch_app_invalid_target_does_not_crash():
    """Verify launch_app reports error safely when target is invalid."""
    app_dict = {
        "name": "Broken App",
        "launch_type": "executable",
        "target": "nonexistent_xyz.exe",
    }
    success, msg = launch_app(app_dict)
    assert not success
    assert "cannot resolve" in msg.lower() or "not found" in msg.lower()


@patch("subprocess.Popen")
def test_launch_app_executable_invoked_safely(mock_popen):
    """Verify subprocess.Popen is called with resolved list and NO shell=True."""
    mock_popen.return_value = MagicMock()
    app_dict = {
        "name": "Notepad",
        "launch_type": "executable",
        "target": "notepad.exe",
        "arguments": ["file.txt"],
        "working_directory": None,
    }

    success, msg = launch_app(app_dict)
    assert success
    mock_popen.assert_called_once()
    called_args, called_kwargs = mock_popen.call_args
    # Command must be a list
    assert isinstance(called_args[0], list)
    assert "notepad.exe" in called_args[0][0].lower()
    assert called_args[0][1:] == ["file.txt"]
    # shell=True MUST NOT be used
    assert called_kwargs.get("shell") is not True


@patch("subprocess.Popen")
def test_launch_app_packaged_invoked_via_shell(mock_popen):
    """Verify packaged app is launched using explorer.exe shell:AppsFolder."""
    mock_popen.return_value = MagicMock()
    app_dict = {
        "name": "Calculator",
        "launch_type": "packaged",
        "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
    }

    success, msg = launch_app(app_dict)
    assert success
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "explorer.exe"
    assert "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App" in cmd[1]


@patch("src.app_launcher.bring_to_front")
@patch("src.app_launcher.find_target_windows")
def test_launch_or_activate_app_brings_existing_to_front(mock_find, mock_bring):
    """Verify that if an existing window is found, it is brought to front without launching a new process."""
    mock_find.return_value = [(12345, "Book1 - Excel")]
    mock_bring.return_value = True

    app_dict = {
        "name": "Microsoft Excel",
        "launch_type": "executable",
        "target": "excel.exe",
    }

    from src.app_launcher import launch_or_activate_app
    with patch("src.app_launcher.launch_app") as mock_launch:
        success, msg = launch_or_activate_app(app_dict)
        assert success
        assert "brought existing" in msg.lower()
        mock_bring.assert_called_once_with(12345)
        mock_launch.assert_not_called()


@patch("src.app_launcher.find_target_windows")
@patch("src.app_launcher.launch_app")
def test_launch_or_activate_app_launches_when_no_existing(mock_launch, mock_find):
    """Verify that if no window is found, launch_app is called to spawn the process."""
    mock_find.return_value = []
    mock_launch.return_value = (True, "Launched successfully.")

    app_dict = {
        "name": "Microsoft Excel",
        "launch_type": "executable",
        "target": "excel.exe",
    }

    from src.app_launcher import launch_or_activate_app
    success, msg = launch_or_activate_app(app_dict)
    assert success
    mock_launch.assert_called_once_with(app_dict)
