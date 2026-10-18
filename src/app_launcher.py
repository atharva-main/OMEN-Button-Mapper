"""Application launcher and configuration manager for OMEN Button Remapper.

Handles configuration loading/saving (with schema validation and atomic file writes),
target validation, and safe application launching for executables, shortcuts, and packaged apps.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from pathlib import Path
from typing import Any

logger = logging.getLogger("omen_remapper.launcher")

CONFIG_VERSION = 1
VALID_LAUNCH_TYPES = {"executable", "shortcut", "packaged"}


def get_config_dir() -> Path:
    """Return the application directory in %LOCALAPPDATA%."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    app_dir = base / "OmenButtonRemapper"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_config_path() -> Path:
    """Return the full path to config.json."""
    return get_config_dir() / "config.json"


def get_log_dir() -> Path:
    """Return the logs directory in %LOCALAPPDATA%."""
    log_dir = get_config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def create_default_config() -> dict[str, Any]:
    """Return a fresh default configuration structure."""
    return {
        "version": CONFIG_VERSION,
        "selected_app": None,
    }


def validate_config(config: Any) -> tuple[bool, str]:
    """Validate configuration dictionary against the version 1 schema.

    Returns (is_valid, error_message).
    """
    if not isinstance(config, dict):
        return False, "Configuration root must be a JSON object."

    version = config.get("version")
    if version != CONFIG_VERSION:
        return False, f"Unsupported configuration version: {version!r}. Expected {CONFIG_VERSION}."

    selected_app = config.get("selected_app")
    if selected_app is None:
        return True, "Valid (no application selected)."

    if not isinstance(selected_app, dict):
        return False, "'selected_app' must be null or an object."

    name = selected_app.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "'selected_app.name' must be a non-empty string."

    launch_type = selected_app.get("launch_type")
    if launch_type not in VALID_LAUNCH_TYPES:
        return False, (
            f"Invalid 'launch_type': {launch_type!r}. "
            f"Must be one of: {', '.join(sorted(VALID_LAUNCH_TYPES))}."
        )

    target = selected_app.get("target")
    if not isinstance(target, str) or not target.strip():
        return False, "'selected_app.target' must be a non-empty string."

    arguments = selected_app.get("arguments")
    if arguments is not None:
        if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
            return False, "'selected_app.arguments' must be a list of strings if provided."

    working_dir = selected_app.get("working_directory")
    if working_dir is not None and not isinstance(working_dir, str):
        return False, "'selected_app.working_directory' must be a string or null."

    return True, "Valid."


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate configuration from the specified or default path.

    If the configuration file does not exist, creates it with a default config.
    If the file is malformed, logs a warning and returns the default config without crashing.
    """
    target_path = Path(path) if path else get_config_path()

    if not target_path.exists():
        default_cfg = create_default_config()
        try:
            save_config(default_cfg, target_path)
        except Exception as e:
            logger.warning("Could not create initial default config at %s: %s", target_path, e)
        return default_cfg

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("Failed to read JSON from %s: %s. Using default config.", target_path, e)
        return create_default_config()

    is_valid, reason = validate_config(data)
    if not is_valid:
        logger.warning(
            "Config at %s failed schema validation: %s. Using default config.",
            target_path,
            reason,
        )
        return create_default_config()

    return data


def save_config(config: dict[str, Any], path: Path | str | None = None) -> None:
    """Validate and atomically save the configuration dictionary.

    Writes to a temporary file in the same directory, flushes, syncs to disk,
    and replaces the existing file atomically. If writing fails, the original
    file remains untouched.
    """
    is_valid, reason = validate_config(config)
    if not is_valid:
        raise ValueError(f"Invalid configuration schema: {reason}")

    target_path = Path(path) if path else get_config_path()
    parent_dir = target_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    # Use NamedTemporaryFile in the same directory to allow atomic os.replace across filesystem boundaries
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=parent_dir,
        delete=False,
        prefix="cfg_tmp_",
        suffix=".json",
    )
    temp_path = Path(temp_file.name)

    try:
        json.dump(config, temp_file, indent=2, ensure_ascii=False)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()

        os.replace(temp_path, target_path)
        logger.info("Successfully updated configuration at %s", target_path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        logger.error("Failed to atomically save config to %s: %s", target_path, e)
        raise IOError(f"Failed to save configuration: {e}") from e


def resolve_executable(target: str) -> str | None:
    """Resolve an executable name or path to a full executable path on Windows.

    Checks:
    1. Direct filesystem path (with environment variables expanded).
    2. PATH environment variable via shutil.which.
    3. Windows Registry App Paths (HKCU and HKLM).
    4. Suffixing .exe if missing and repeating steps.
    """
    if not target or not isinstance(target, str):
        return None

    cleaned = os.path.expandvars(target.strip().strip('"'))

    # 1. Direct path check
    if os.path.isfile(cleaned):
        return os.path.abspath(cleaned)

    # 2. PATH resolution
    which_path = shutil.which(cleaned)
    if which_path and os.path.isfile(which_path):
        return os.path.abspath(which_path)

    # 3. Windows Registry App Paths
    base_name = os.path.basename(cleaned)
    for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for candidate in (base_name, f"{base_name}.exe"):
            try:
                key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{candidate}"
                with winreg.OpenKey(root_key, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val:
                        resolved = os.path.expandvars(val.strip().strip('"'))
                        if os.path.isfile(resolved):
                            return os.path.abspath(resolved)
            except OSError:
                continue

    # 4. Try appending .exe if not present
    if not cleaned.lower().endswith(".exe"):
        return resolve_executable(cleaned + ".exe")

    return None


def validate_target(app_dict: dict[str, Any]) -> tuple[bool, str]:
    """Validate an application descriptor's launch information before saving or launching.

    Returns (is_valid, message).
    """
    if not isinstance(app_dict, dict):
        return False, "Target specification must be an object."

    launch_type = app_dict.get("launch_type")
    if launch_type not in VALID_LAUNCH_TYPES:
        return False, f"Unsupported launch type: {launch_type!r}"

    target = app_dict.get("target")
    if not target or not isinstance(target, str):
        return False, "Target path or identifier is missing."

    working_dir = app_dict.get("working_directory")
    if working_dir:
        expanded_dir = os.path.expandvars(working_dir.strip().strip('"'))
        if not os.path.isdir(expanded_dir):
            return False, f"Working directory does not exist: {expanded_dir}"

    if launch_type == "executable":
        resolved = resolve_executable(target)
        if not resolved:
            return False, f"Cannot resolve executable target: {target}"
        return True, f"Valid executable: {resolved}"

    if launch_type == "shortcut":
        expanded_target = os.path.expandvars(target.strip().strip('"'))
        if os.path.isfile(expanded_target):
            return True, f"Valid shortcut: {expanded_target}"
        # Shortcut might also point directly to an executable
        resolved = resolve_executable(target)
        if resolved:
            return True, f"Valid target: {resolved}"
        return False, f"Shortcut target not found: {target}"

    if launch_type == "packaged":
        # AUMID typically contains an exclamation mark separating package and app ID
        if "!" not in target:
            return False, f"Invalid AUMID format for packaged application: {target}"
        return True, f"Valid packaged app AUMID: {target}"

    return False, "Unknown launch validation error."


def launch_app(app_dict: dict[str, Any]) -> tuple[bool, str]:
    """Safely launch an application according to its specification.

    Does NOT use shell=True. Returns (success, status_message).
    """
    is_valid, validation_msg = validate_target(app_dict)
    if not is_valid:
        logger.error("Launch validation failed: %s", validation_msg)
        return False, validation_msg

    launch_type = app_dict["launch_type"]
    target = app_dict["target"]
    arguments = app_dict.get("arguments") or []
    working_dir = app_dict.get("working_directory")
    if working_dir:
        working_dir = os.path.expandvars(working_dir.strip().strip('"'))
        if not os.path.isdir(working_dir):
            working_dir = None

    name = app_dict.get("name", target)

    try:
        if launch_type == "executable":
            resolved_exe = resolve_executable(target)
            if not resolved_exe:
                return False, f"Could not find executable: {target}"

            cmd = [resolved_exe] + list(arguments)
            logger.info("Launching executable: %s with cwd=%s", cmd, working_dir)
            subprocess.Popen(
                cmd,
                cwd=working_dir,
                close_fds=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return True, f"Launched {name} successfully."

        elif launch_type == "shortcut":
            expanded_lnk = os.path.expandvars(target.strip().strip('"'))
            # If arguments are provided or target is directly executable, use subprocess
            if arguments or (not expanded_lnk.lower().endswith(".lnk") and os.path.isfile(expanded_lnk)):
                resolved = resolve_executable(expanded_lnk) or expanded_lnk
                cmd = [resolved] + list(arguments)
                logger.info("Launching shortcut target: %s with cwd=%s", cmd, working_dir)
                subprocess.Popen(
                    cmd,
                    cwd=working_dir,
                    close_fds=True,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                # Use Windows shell to open the .lnk file
                logger.info("Opening shortcut file: %s", expanded_lnk)
                os.startfile(expanded_lnk)
            return True, f"Launched {name} successfully."

        elif launch_type == "packaged":
            # Packaged app launching via explorer.exe shell:AppsFolder\<AUMID>
            # This is the standard, documented Windows mechanism to activate packaged apps from user space.
            aumid_target = f"shell:AppsFolder\\{target}"
            logger.info("Launching packaged app via shell: %s", aumid_target)

            # Using subprocess to launch explorer.exe with the AppsFolder target
            subprocess.Popen(
                ["explorer.exe", aumid_target],
                cwd=working_dir,
                close_fds=True,
            )
            return True, f"Launched {name} successfully."

        return False, f"Unknown launch type: {launch_type}"

    except Exception as e:
        err_msg = f"Failed to launch '{name}': {e}"
        logger.error(err_msg, exc_info=True)
        return False, err_msg


def ensure_default_desktop() -> None:
    """Ensure current thread is attached to the user's interactive 'Default' desktop."""
    try:
        import win32service, win32con, ctypes
        hdesk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
        ctypes.windll.user32.SetThreadDesktop(int(hdesk))
    except Exception as e:
        logger.debug("Could not switch thread desktop: %s", e)


def bring_to_front(hwnd: int) -> bool:
    """Force an existing window to the foreground, restoring if minimized."""
    try:
        import ctypes, win32gui, win32con, win32api
        user32 = ctypes.windll.user32

        # Allow foreground activation
        user32.AllowSetForegroundWindow(-1)

        # Bypass Windows foreground lock timeout by simulating benign Alt key
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

        # If minimized, restore it
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        win32gui.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        logger.debug("bring_to_front failed on HWND %d: %s", hwnd, e)
        return False


def find_target_windows(app_dict: dict[str, Any]) -> list[tuple[int, str]]:
    """Locate open visible top-level windows belonging to the specified application.

    Returns a list of (hwnd, window_title) tuples.
    """
    ensure_default_desktop()

    target = (app_dict.get("target") or "").strip().lower()
    target_base = os.path.basename(target).replace(".exe", "")
    app_name = (app_dict.get("name") or "").strip().lower()

    if app_dict.get("launch_type") == "executable":
        resolved = resolve_executable(target)
        if resolved:
            target_base = os.path.basename(resolved).lower().replace(".exe", "")

    matched: list[tuple[int, str]] = []

    try:
        import win32gui, win32con, win32process, psutil

        def enum_cb(hwnd, extra):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
                return True

            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                p = psutil.Process(pid)
                pname = p.name().lower()
                pexe = (p.exe() or "").lower()

                # 1. Match process name or executable path
                if target_base and (target_base in pname or target_base in pexe):
                    matched.append((hwnd, title))
                    return True

                # 2. Modern packaged Store app (runs inside ApplicationFrameHost.exe)
                if "applicationframehost" in pname:
                    if app_name and len(app_name) >= 3 and app_name in title.lower():
                        matched.append((hwnd, title))
                        return True

                # 3. Match window title with app display name (for multi-process apps)
                if app_name and len(app_name) >= 4 and app_name in title.lower():
                    matched.append((hwnd, title))
                    return True

            except Exception:
                pass

            return True

        win32gui.EnumWindows(enum_cb, None)
    except Exception as e:
        logger.debug("EnumWindows failed: %s", e)

    return matched


def launch_or_activate_app(app_dict: dict[str, Any]) -> tuple[bool, str]:
    """Smart activation: bring existing window to front, or launch and force foreground."""
    import time, threading

    app_name = app_dict.get("name", "Application")

    # 1. Check if application already has an open window
    try:
        existing = find_target_windows(app_dict)
        if existing:
            hwnd, title = existing[0]
            logger.info("Found existing window %d ('%s') for '%s'. Bringing to front.", hwnd, title, app_name)
            bring_to_front(hwnd)
            return True, f"Brought existing {app_name} ('{title}') to foreground."
    except Exception as e:
        logger.debug("Error while searching for existing window: %s", e)

    # 2. Unlock foreground permission for newly spawned process
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass

    # 3. Launch process
    success, msg = launch_app(app_dict)

    if success:
        # 4. Watch for newly created window and force it to foreground
        def ensure_foreground():
            t_end = time.time() + 2.5
            while time.time() < t_end:
                windows = find_target_windows(app_dict)
                if windows:
                    bring_to_front(windows[0][0])
                    break
                time.sleep(0.12)

        threading.Thread(target=ensure_foreground, daemon=True).start()

    return success, msg
