"""Unit tests for configuration loading, validation, and atomic saving."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.app_launcher import (
    CONFIG_VERSION,
    create_default_config,
    load_config,
    save_config,
    validate_config,
)


def test_default_config_structure():
    """Verify the default configuration matches version 1 schema."""
    cfg = create_default_config()
    assert cfg["version"] == CONFIG_VERSION
    assert cfg["selected_app"] is None
    is_valid, msg = validate_config(cfg)
    assert is_valid, msg


def test_valid_executable_config():
    """Verify valid configuration containing an executable target passes validation."""
    cfg = {
        "version": 1,
        "selected_app": {
            "name": "Microsoft Excel",
            "launch_type": "executable",
            "target": "excel.exe",
            "arguments": ["/r", "report.xlsx"],
            "working_directory": None,
        },
    }
    is_valid, msg = validate_config(cfg)
    assert is_valid, msg


def test_valid_packaged_app_config():
    """Verify valid configuration containing a packaged app passes validation."""
    cfg = {
        "version": 1,
        "selected_app": {
            "name": "Calculator",
            "launch_type": "packaged",
            "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
            "arguments": [],
            "working_directory": None,
        },
    }
    is_valid, msg = validate_config(cfg)
    assert is_valid, msg


@pytest.mark.parametrize(
    "invalid_cfg,expected_error_substr",
    [
        ("not a dict", "must be a JSON object"),
        ({"version": 99, "selected_app": None}, "Unsupported configuration version"),
        ({"version": 1, "selected_app": "not a dict"}, "must be null or an object"),
        ({"version": 1, "selected_app": {"launch_type": "executable", "target": "notepad.exe"}}, "name"),
        ({"version": 1, "selected_app": {"name": "Test", "launch_type": "invalid_type", "target": "notepad.exe"}}, "Invalid 'launch_type'"),
        ({"version": 1, "selected_app": {"name": "Test", "launch_type": "executable", "target": ""}}, "target"),
        ({"version": 1, "selected_app": {"name": "Test", "launch_type": "executable", "target": "notepad.exe", "arguments": "not-a-list"}}, "arguments"),
        ({"version": 1, "selected_app": {"name": "Test", "launch_type": "executable", "target": "notepad.exe", "working_directory": 123}}, "working_directory"),
    ],
)
def test_invalid_config_schema(invalid_cfg, expected_error_substr):
    """Verify invalid configuration variants are rejected with clear error messages."""
    is_valid, msg = validate_config(invalid_cfg)
    assert not is_valid
    assert expected_error_substr.lower() in msg.lower()


def test_atomic_saving(tmp_path: Path):
    """Verify atomic configuration saving writes valid JSON and creates parent dirs."""
    cfg_file = tmp_path / "subdir" / "test_config.json"
    data = {
        "version": 1,
        "selected_app": {
            "name": "Notepad",
            "launch_type": "executable",
            "target": "notepad.exe",
            "arguments": [],
            "working_directory": None,
        },
    }

    save_config(data, cfg_file)
    assert cfg_file.exists()

    with open(cfg_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded == data


def test_atomic_saving_preserves_on_failure(tmp_path: Path):
    """Verify that attempting to save an invalid configuration preserves previous valid config."""
    cfg_file = tmp_path / "config.json"
    valid_data = {
        "version": 1,
        "selected_app": {
            "name": "Valid App",
            "launch_type": "executable",
            "target": "valid.exe",
            "arguments": [],
            "working_directory": None,
        },
    }
    save_config(valid_data, cfg_file)

    invalid_data = {
        "version": 1,
        "selected_app": {
            "name": "",  # invalid empty name
            "launch_type": "executable",
            "target": "valid.exe",
        },
    }

    with pytest.raises(ValueError):
        save_config(invalid_data, cfg_file)

    # Confirm original file content remains untouched
    with open(cfg_file, "r", encoding="utf-8") as f:
        current_data = json.load(f)
    assert current_data == valid_data


def test_load_missing_config(tmp_path: Path):
    """Loading a missing config creates default configuration without crashing."""
    missing_file = tmp_path / "does_not_exist.json"
    cfg = load_config(missing_file)
    assert cfg["version"] == 1
    assert cfg["selected_app"] is None
    assert missing_file.exists()


def test_load_corrupted_config(tmp_path: Path):
    """Loading corrupted JSON falls back to default configuration gracefully."""
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{ this is not valid json :", encoding="utf-8")

    cfg = load_config(corrupt_file)
    assert cfg["version"] == 1
    assert cfg["selected_app"] is None
