"""Hardware compatibility and OMEN button event detection module.

Provides system inspection, WMI driver verification, HP software conflict detection,
and an interactive live key-press listener to verify OMEN hardware events.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
import winreg
from typing import Any

import psutil

logger = logging.getLogger("omen_remapper.compatibility")

# Standard verified event for HP Victus 15 and modern HP OMEN laptops
STANDARD_EVENT_ID = 29
STANDARD_EVENT_DATA = 8613

# Known conflicting HP background software that intercepts the OMEN key
CONFLICTING_PROCESSES = {
    "omencommandcenter.exe": "OMEN Gaming Hub (main process)",
    "omengaminghub.exe": "OMEN Gaming Hub (executable)",
    "hp.omen.omengaminghub.exe": "OMEN Gaming Hub (packaged UWP app)",
    "omenhsaclient.exe": "OMEN HSA Client",
    "hpsysevent.exe": "HP System Event Utility",
    "hp system event utility.exe": "HP System Event Utility",
    "hp.systemeventutility.exe": "HP System Event Utility (UWP)",
    "hphsa.exe": "HP HSA Service",
}


def get_hardware_info() -> dict[str, Any]:
    """Retrieve system, manufacturer, model, and OS details via Windows Registry."""
    info: dict[str, Any] = {
        "manufacturer": "Unknown",
        "model": "Unknown",
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "architecture": platform.machine(),
        "is_hp": False,
    }

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS"
        ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, "SystemManufacturer")
                if val:
                    info["manufacturer"] = str(val).strip()
            except OSError:
                pass

            try:
                val, _ = winreg.QueryValueEx(key, "SystemProductName")
                if val:
                    info["model"] = str(val).strip()
            except OSError:
                pass
    except Exception as e:
        logger.debug("Failed to query BIOS registry: %s", e)

    manuf_lower = info["manufacturer"].lower()
    model_lower = info["model"].lower()
    if "hp" in manuf_lower or "hewlett" in manuf_lower or "victus" in model_lower or "omen" in model_lower:
        info["is_hp"] = True

    return info


def check_wmi_hp_driver() -> tuple[bool, str]:
    """Check whether the HP WMI ACPI event provider ('hpqBEvnt') is available in root\\wmi."""
    try:
        import win32com.client
        import pythoncom

        pythoncom.CoInitialize()
        wmi = None
        cls = None
        try:
            wmi = win32com.client.GetObject("winmgmts://./root/wmi")
            cls = wmi.Get("hpqBEvnt")
            class_name = str(getattr(cls.Path_, "Class", "hpqBEvnt"))
            return True, f"HP WMI Event Class '{class_name}' is available."
        finally:
            del cls
            del wmi
            pythoncom.CoUninitialize()
    except Exception as e:
        err_msg = str(e)
        logger.debug("hpqBEvnt check failed: %s", err_msg)
        return (
            False,
            "hpqBEvnt WMI class not found. Ensure 'HP Application Enabling Services' driver is installed.",
        )


def check_hp_conflicts() -> list[dict[str, str]]:
    """Scan running processes for HP software known to intercept the OMEN key."""
    detected: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            pname = (proc.info.get("name") or "").strip().lower()
            if pname in CONFLICTING_PROCESSES and pname not in seen_names:
                seen_names.add(pname)
                desc = CONFLICTING_PROCESSES[pname]
                detected.append({
                    "process": pname,
                    "description": desc,
                    "advice": f"Close or disable '{desc}' to prevent HP software from capturing the key.",
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return detected


def classify_event(event_id: int | None, event_data: int | None) -> tuple[bool, str]:
    """Classify detected WMI event codes."""
    if event_id == STANDARD_EVENT_ID and event_data == STANDARD_EVENT_DATA:
        return (
            True,
            f"Standard OMEN key event verified (EventID={event_id}, EventData={event_data}). 100% compatible!",
        )
    elif event_id == STANDARD_EVENT_ID:
        return (
            True,
            f"OMEN key event detected (EventID={event_id}, EventData={event_data}). Compatible.",
        )
    else:
        return (
            False,
            f"Detected non-standard event (EventID={event_id}, EventData={event_data}).",
        )


def listen_for_single_wmi_event(
    stop_event: threading.Event, timeout_seconds: float = 30.0
) -> tuple[int | None, int | None, str]:
    """Listen for a single hpqBEvnt notification query.

    Returns:
        (event_id, event_data, message)
    """
    try:
        import win32com.client
        import pythoncom

        pythoncom.CoInitialize()
        wmi = None
        events = None
        try:
            wmi = win32com.client.GetObject("winmgmts://./root/wmi")
            wql = "SELECT * FROM hpqBEvnt"
            events = wmi.ExecNotificationQuery(wql)

            start_time = time.time()
            while not stop_event.is_set():
                if time.time() - start_time > timeout_seconds:
                    return None, None, "Listening timed out after 30 seconds."

                try:
                    event = events.NextEvent(500)
                    raw_id = getattr(event, "EventID", None)
                    raw_data = getattr(event, "EventData", None)

                    event_id = int(raw_id) if raw_id is not None else None
                    event_data = int(raw_data) if raw_data is not None else None

                    return event_id, event_data, "Event captured successfully."
                except Exception as e:
                    # Timeout from NextEvent or continuing
                    if "timed out" in str(e).lower() or "-2147217357" in str(e):
                        continue
                    if stop_event.is_set():
                        break
                    raise

            return None, None, "Listening cancelled."
        finally:
            del events
            del wmi
            pythoncom.CoUninitialize()
    except Exception as e:
        return None, None, f"WMI listener error: {e}"
