"""Background listener for OMEN keyboard button events.

Subscribes to WMI events from HP WMI event class hpqBEvnt (EventID=29, EventData=8613),
reads current configuration dynamically on button press, and launches the remapped application.
Runs as a lightweight background process with single-instance enforcement.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pythoncom
import pywintypes
import win32com.client

try:
    from app_launcher import get_log_dir, launch_or_activate_app, load_config
except ImportError:
    from src.app_launcher import get_log_dir, launch_or_activate_app, load_config

# Single-instance mutex name
LISTENER_MUTEX_NAME = "Local\\OmenButtonRemapperListenerMutex"
ERROR_ALREADY_EXISTS = 183

# WMI Event specification
WMI_NAMESPACE = "winmgmts://./root/wmi"
OMEN_EVENT_QUERY = "SELECT * FROM hpqBEvnt WHERE EventID = 29 AND EventData = 8613"
EXPECTED_EVENT_ID = 29
EXPECTED_EVENT_DATA = 8613


def setup_logger(debug: bool = False) -> logging.Logger:
    """Configure rotating file logger and optional console logging."""
    logger = logging.getLogger("omen_listener")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    log_path = get_log_dir() / "listener.log"

    # Rotating file handler: 1MB limit with 3 backups
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_048_576,
        backupCount=3,
        encoding="utf-8",
    )
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [PID %(process)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler for interactive debugging (only if stderr is available)
    if sys.stderr is not None and (debug or (hasattr(sys.stderr, "isatty") and sys.stderr.isatty())):
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(file_formatter)
        logger.addHandler(console_handler)

    return logger


logger = setup_logger()


class SingleInstanceMutex:
    """Windows named mutex to ensure only one instance of the listener runs."""

    def __init__(self, name: str = LISTENER_MUTEX_NAME):
        self.name = name
        self.handle: wintypes.HANDLE | None = None
        self.is_owner = False

    def acquire(self) -> bool:
        """Attempt to acquire ownership of the named mutex."""
        kernel32 = ctypes.windll.kernel32
        self.handle = kernel32.CreateMutexW(None, True, self.name)
        if not self.handle:
            logger.error("Failed to create named mutex: error code %d", kernel32.GetLastError())
            return False

        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            logger.warning("Another instance of OmenButtonListener is already running.")
            self.release()
            return False

        self.is_owner = True
        return True

    def release(self) -> None:
        """Release and close the mutex handle."""
        if self.handle:
            kernel32 = ctypes.windll.kernel32
            if self.is_owner:
                kernel32.ReleaseMutex(self.handle)
                self.is_owner = False
            kernel32.CloseHandle(self.handle)
            self.handle = None


class OmenButtonListener:
    """Listens for HP OMEN button WMI events and executes remapped actions."""

    def __init__(self, debounce_interval: float = 0.40):
        self.debounce_interval = debounce_interval
        self._last_event_time: float = 0.0
        self._stop_event = threading.Event()
        self._mutex = SingleInstanceMutex()
        self._connected = False

    def is_valid_omen_event(self, event_obj: Any) -> bool:
        """Verify that the WMI event matches the verified OMEN button signature."""
        try:
            event_id = getattr(event_obj, "EventID", None)
            event_data = getattr(event_obj, "EventData", None)

            if event_id is None or event_data is None:
                # Some WMI providers expose properties as item index
                props = getattr(event_obj, "Properties_", None)
                if props:
                    for p in props:
                        if p.Name == "EventID":
                            event_id = p.Value
                        elif p.Name == "EventData":
                            event_data = p.Value

            return int(event_id) == EXPECTED_EVENT_ID and int(event_data) == EXPECTED_EVENT_DATA
        except Exception as e:
            logger.debug("Error inspecting WMI event properties: %s", e)
            return False

    def on_button_press(self) -> None:
        """Handle verified OMEN button press: debounce, read configuration, and launch app."""
        now = time.monotonic()
        if (now - self._last_event_time) < self.debounce_interval:
            logger.debug("Debouncing duplicate button press within %.2fs", self.debounce_interval)
            return

        self._last_event_time = now
        logger.info("OMEN button event verified (EventID=%d, EventData=%d)", EXPECTED_EVENT_ID, EXPECTED_EVENT_DATA)

        # Dynamic configuration read: reflects user changes immediately without listener restart
        try:
            config = load_config()
            selected_app = config.get("selected_app")

            if not selected_app:
                logger.warning("OMEN button pressed, but no application is configured.")
                return

            app_name = selected_app.get("name", "Unknown App")
            logger.info("Executing remapped action for '%s'", app_name)

            success, message = launch_or_activate_app(selected_app)
            if success:
                logger.info("Activation succeeded: %s", message)
            else:
                logger.error("Activation failed: %s", message)

        except Exception as e:
            logger.error("Unexpected error during button press handling: %s", e, exc_info=True)

    def stop(self) -> None:
        """Signal the listener loop to terminate cleanly."""
        logger.info("Stopping OmenButtonListener...")
        self._stop_event.set()

    def run(self) -> int:
        """Run the main event subscription loop with automatic error recovery."""
        if not self._mutex.acquire():
            logger.info("Listener already running in another process. Exiting cleanly.")
            return 0

        logger.info("OmenButtonListener started (PID: %d)", os.getpid())

        # Setup OS signal handlers
        def signal_handler(signum, frame):
            logger.info("Received signal %d. Initiating clean shutdown.", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except Exception as sig_err:
            logger.debug("Could not attach standard signal handlers: %s", sig_err)

        # Optional Win32 console handler
        try:
            import win32api

            def console_ctrl_handler(ctrl_type):
                logger.info("Received console ctrl event: %d. Shutting down.", ctrl_type)
                self.stop()
                return True

            win32api.SetConsoleCtrlHandler(console_ctrl_handler, True)
        except Exception:
            pass

        try:
            while not self._stop_event.is_set():
                try:
                    logger.info("Connecting to WMI namespace '%s'...", WMI_NAMESPACE)
                    try:
                        pythoncom.CoInitialize()
                    except Exception:
                        pass
                    wmi = win32com.client.GetObject(WMI_NAMESPACE)
                    event_sink = wmi.ExecNotificationQuery(OMEN_EVENT_QUERY)
                    self._connected = True
                    logger.info("WMI notification query registered successfully. Waiting for button events...")

                    while not self._stop_event.is_set():
                        try:
                            # 1000ms timeout per wait to allow checking _stop_event regularly
                            event_obj = event_sink.NextEvent(1000)
                            if self.is_valid_omen_event(event_obj):
                                self.on_button_press()
                            else:
                                logger.debug("Received WMI event that did not match expected OMEN signature.")
                        except pywintypes.com_error as com_err:
                            err_str = str(com_err).lower()
                            # Standard WMI timeout (WBEM_E_TIMEDOUT / 0x80041032 / DISP_E_EXCEPTION)
                            if "timed out" in err_str:
                                continue
                            logger.warning("WMI COM exception during NextEvent: %s", com_err)
                            raise

                except Exception as loop_err:
                    self._connected = False
                    if self._stop_event.is_set():
                        break
                    logger.warning(
                        "WMI connection lost or error occurred: %s. Re-subscribing in 5 seconds...",
                        loop_err,
                    )
                    time.sleep(5)
                finally:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

        finally:
            self._mutex.release()
            logger.info("OmenButtonListener shut down cleanly.")

        return 0


def main() -> int:
    """Entrypoint for OmenButtonListener."""
    try:
        parser = argparse.ArgumentParser(description="OMEN Button Remapper Background Listener")
        parser.add_argument("--debug", action="store_true", help="Enable debug logging output")
        args = parser.parse_args()

        global logger
        if args.debug:
            logger = setup_logger(debug=True)

        listener = OmenButtonListener()
        return listener.run()
    except Exception as e:
        if logger:
            logger.critical("Unhandled top-level exception in listener: %s", e, exc_info=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
