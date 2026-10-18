"""Application discovery module for OMEN Button Remapper.

Discovers desktop applications (Start Menu shortcuts) and Microsoft Store
packaged applications (AUMIDs), extracting metadata, display names, and launch parameters.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("omen_remapper.discovery")

# Target extensions that indicate non-application / documentation targets
IGNORED_EXTENSIONS = {
    ".chm",
    ".url",
    ".txt",
    ".pdf",
    ".html",
    ".htm",
    ".rtf",
    ".doc",
    ".docx",
    ".hlp",
}


@dataclass
class DiscoveredApp:
    """Represents an application discovered on the system."""

    display_name: str
    app_id: str
    launch_type: str  # "executable", "shortcut", "packaged"
    target: str
    arguments: list[str] = field(default_factory=list)
    working_directory: str | None = None
    icon_source: str | None = None

    def to_config_dict(self) -> dict[str, Any]:
        """Convert into the serializable configuration dictionary."""
        return {
            "name": self.display_name,
            "launch_type": self.launch_type,
            "target": self.target,
            "arguments": self.arguments,
            "working_directory": self.working_directory,
        }


def clean_display_name(name: str) -> str:
    """Clean resource URI strings or formatting artifacts from display names."""
    if not name:
        return ""

    # Handle ms-resource:// URIs
    if name.startswith("ms-resource:"):
        match = re.search(r"([^/]+?)(?:_DisplayName)?$", name)
        if match:
            extracted = match.group(1)
            # Remove common prefixes/suffixes
            extracted = re.sub(r"^(AppManifest|Resources)_", "", extracted)
            extracted = re.sub(r"_(DisplayName|Desktop)$", "", extracted)
            extracted = re.sub(r"Desktop$", "", extracted)
            extracted = re.sub(r"DisplayName$", "", extracted)
            return extracted.strip() or name

    return name.strip()


def is_uninstaller_or_support(name: str, target: str) -> bool:
    """Identify and filter out uninstaller or documentation shortcuts."""
    lower_name = name.lower()
    lower_target = target.lower()

    if "uninstall" in lower_name or "uninstall" in lower_target:
        return True
    if "unins000" in lower_target or "uninst" in lower_target:
        return True
    if any(lower_target.endswith(ext) for ext in IGNORED_EXTENSIONS):
        return True

    return False


def discover_start_menu_shortcuts() -> list[DiscoveredApp]:
    """Scan Start Menu folders for valid .lnk shortcuts."""
    apps: list[DiscoveredApp] = []

    search_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]

    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception as e:
        logger.error("Failed to initialize WScript.Shell for shortcut resolution: %s", e)
        return apps

    for base_dir in search_dirs:
        if not os.path.exists(base_dir):
            continue

        for root, _, files in os.walk(base_dir):
            for file_name in files:
                if not file_name.lower().endswith(".lnk"):
                    continue

                full_lnk_path = os.path.join(root, file_name)
                display_name = file_name[:-4]

                try:
                    shortcut = shell.CreateShortcut(full_lnk_path)
                    target = (shortcut.TargetPath or "").strip()

                    if not target:
                        continue

                    if is_uninstaller_or_support(display_name, target):
                        continue

                    # Parse arguments safely
                    raw_args = (shortcut.Arguments or "").strip()
                    args_list = []
                    if raw_args:
                        try:
                            args_list = shlex.split(raw_args, posix=False)
                        except Exception:
                            args_list = [raw_args]

                    working_dir = (shortcut.WorkingDirectory or "").strip() or None
                    icon_loc = (shortcut.IconLocation or "").strip()
                    icon_source = icon_loc if icon_loc else (target if os.path.exists(target) else full_lnk_path)

                    app_id = f"lnk:{full_lnk_path.lower()}"
                    launch_type = "executable" if target.lower().endswith(".exe") else "shortcut"

                    apps.append(
                        DiscoveredApp(
                            display_name=display_name,
                            app_id=app_id,
                            launch_type=launch_type,
                            target=target if launch_type == "executable" else full_lnk_path,
                            arguments=args_list,
                            working_directory=working_dir,
                            icon_source=icon_source,
                        )
                    )
                except Exception as e:
                    logger.debug("Skipping shortcut %s: %s", full_lnk_path, e)
                    continue

    return apps


def discover_packaged_apps() -> list[DiscoveredApp]:
    """Discover Microsoft Store and modern packaged applications (UWP/MSIX) via shell:AppsFolder."""
    apps: list[DiscoveredApp] = []

    try:
        import win32com.client
        shell = win32com.client.Dispatch("Shell.Application")
        apps_folder = shell.NameSpace("shell:AppsFolder")
        if not apps_folder:
            return apps

        for item in apps_folder.Items():
            try:
                raw_path = str(item.Path or "")
                raw_name = str(item.Name or "")

                # Packaged apps have an AUMID containing '!'
                if "!" not in raw_path:
                    continue

                display_name = clean_display_name(raw_name)
                if not display_name or is_uninstaller_or_support(display_name, raw_path):
                    continue

                # Exclude purely internal or non-launchable background packages
                if "Host" in raw_path and not display_name:
                    continue

                app_id = f"packaged:{raw_path.lower()}"
                apps.append(
                    DiscoveredApp(
                        display_name=display_name,
                        app_id=app_id,
                        launch_type="packaged",
                        target=raw_path,
                        arguments=[],
                        working_directory=None,
                        icon_source=f"shell:AppsFolder\\{raw_path}",
                    )
                )
            except Exception as item_err:
                logger.debug("Skipping packaged app item: %s", item_err)
                continue

    except Exception as e:
        logger.warning("Packaged app discovery via Shell.Application encountered an error: %s", e)

    return apps


def deduplicate_applications(apps: list[DiscoveredApp]) -> list[DiscoveredApp]:
    """Remove duplicate application entries that resolve to the same launch target or AUMID."""
    seen_targets: set[str] = set()
    deduped: list[DiscoveredApp] = []

    for app in apps:
        norm_target = app.target.strip().lower()
        if not norm_target:
            continue

        # Composite key: type + target
        key = f"{app.launch_type}:{norm_target}"
        if key in seen_targets:
            continue

        seen_targets.add(key)
        deduped.append(app)

    return deduped


def sort_applications(apps: list[DiscoveredApp], ascending: bool = True) -> list[DiscoveredApp]:
    """Sort applications predictably by display name."""
    return sorted(apps, key=lambda a: a.display_name.lower(), reverse=not ascending)


def filter_applications(apps: list[DiscoveredApp], query: str) -> list[DiscoveredApp]:
    """Filter applications by search query without modifying original list."""
    q = query.strip().lower()
    if not q:
        return apps
    return [a for a in apps if q in a.display_name.lower()]


def discover_all_applications(ascending: bool = True) -> list[DiscoveredApp]:
    """Discover, deduplicate, and sort all desktop and packaged applications."""
    desktop_apps = discover_start_menu_shortcuts()
    packaged_apps = discover_packaged_apps()

    all_apps = deduplicate_applications(desktop_apps + packaged_apps)
    return sort_applications(all_apps, ascending=ascending)
