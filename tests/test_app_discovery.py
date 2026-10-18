"""Unit tests for application discovery, deduplication, sorting, and search filtering."""

from __future__ import annotations

import pytest

from src.app_discovery import (
    DiscoveredApp,
    clean_display_name,
    deduplicate_applications,
    filter_applications,
    is_uninstaller_or_support,
    sort_applications,
)


def test_discovered_app_to_config_dict():
    """Verify to_config_dict only produces clean, serializable configuration fields."""
    app = DiscoveredApp(
        display_name="Test Tool",
        app_id="test:123",
        launch_type="executable",
        target="C:\\Tools\\test.exe",
        arguments=["--verbose"],
        working_directory="C:\\Tools",
        icon_source="C:\\Tools\\test.ico",
    )

    cfg_dict = app.to_config_dict()
    assert cfg_dict == {
        "name": "Test Tool",
        "launch_type": "executable",
        "target": "C:\\Tools\\test.exe",
        "arguments": ["--verbose"],
        "working_directory": "C:\\Tools",
    }
    # Ensure transient properties (app_id, icon_source) are omitted from config
    assert "icon_source" not in cfg_dict
    assert "app_id" not in cfg_dict


def test_clean_display_name():
    """Verify resource URIs and mangled display names are cleaned."""
    assert clean_display_name("Normal App") == "Normal App"
    assert clean_display_name("ms-resource://AppManifest_MailDesktop_DisplayName") == "Mail"
    assert clean_display_name("ms-resource://Calculator_DisplayName") == "Calculator"


def test_uninstaller_detection():
    """Verify uninstallers and help documents are identified for exclusion."""
    assert is_uninstaller_or_support("Uninstall App", "C:\\App\\unins000.exe")
    assert is_uninstaller_or_support("App Help", "C:\\App\\help.chm")
    assert is_uninstaller_or_support("Documentation", "C:\\App\\manual.pdf")
    assert not is_uninstaller_or_support("Calculator", "C:\\Windows\\System32\\calc.exe")


def test_deduplicate_applications():
    """Verify applications with identical launch targets and types are deduplicated."""
    apps = [
        DiscoveredApp(
            display_name="App Instance 1",
            app_id="lnk:1",
            launch_type="executable",
            target="C:\\Program Files\\App\\app.exe",
        ),
        DiscoveredApp(
            display_name="App Instance 2",
            app_id="lnk:2",
            launch_type="executable",
            target="C:\\Program Files\\App\\app.exe",  # duplicate target
        ),
        DiscoveredApp(
            display_name="Other App",
            app_id="lnk:3",
            launch_type="executable",
            target="C:\\Program Files\\Other\\other.exe",
        ),
    ]

    deduped = deduplicate_applications(apps)
    assert len(deduped) == 2
    assert deduped[0].display_name == "App Instance 1"
    assert deduped[1].display_name == "Other App"


def test_sort_applications():
    """Verify sorting applications alphabetically ascending and descending."""
    apps = [
        DiscoveredApp(display_name="Zebra", app_id="1", launch_type="executable", target="z.exe"),
        DiscoveredApp(display_name="Apple", app_id="2", launch_type="executable", target="a.exe"),
        DiscoveredApp(display_name="Mango", app_id="3", launch_type="executable", target="m.exe"),
    ]

    asc = sort_applications(apps, ascending=True)
    assert [a.display_name for a in asc] == ["Apple", "Mango", "Zebra"]

    desc = sort_applications(apps, ascending=False)
    assert [a.display_name for a in desc] == ["Zebra", "Mango", "Apple"]


def test_filter_applications():
    """Verify search filtering matches display names case-insensitively."""
    apps = [
        DiscoveredApp(display_name="Microsoft Excel", app_id="1", launch_type="executable", target="excel.exe"),
        DiscoveredApp(display_name="Microsoft Word", app_id="2", launch_type="executable", target="winword.exe"),
        DiscoveredApp(display_name="Google Chrome", app_id="3", launch_type="executable", target="chrome.exe"),
    ]

    assert len(filter_applications(apps, "micro")) == 2
    assert len(filter_applications(apps, "excel")) == 1
    assert filter_applications(apps, "excel")[0].display_name == "Microsoft Excel"
    assert len(filter_applications(apps, "xyz")) == 0
    assert len(filter_applications(apps, "")) == 3
