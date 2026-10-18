"""PySide6 Configurator GUI for OMEN Button Remapper.

Minimalist traditional Windows service applet interface:
- Ultra-low memory footprint and instant startup.
- Clean native Windows controls and system palette.
- Background listener control (Start, Stop, Restart).
- Windows Task Scheduler sign-in trigger toggle.
- Searchable application mapping with 16x16 icons and atomic configuration save.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from PySide6.QtCore import QFileInfo, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

try:
    from app_discovery import (
        DiscoveredApp,
        discover_all_applications,
        sort_applications,
    )
    from app_launcher import (
        get_config_path,
        get_log_dir,
        load_config,
        save_config,
        validate_target,
    )
except ImportError:
    from src.app_discovery import (
        DiscoveredApp,
        discover_all_applications,
        sort_applications,
    )
    from src.app_launcher import (
        get_config_path,
        get_log_dir,
        load_config,
        save_config,
        validate_target,
    )

logger = logging.getLogger("omen_remapper.configurator")
SCHEDULED_TASK_NAME = "OmenButtonRemapperListener"


def get_asset_path(filename: str) -> Path:
    """Resolve asset paths in dev and PyInstaller."""
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        candidate = base / "assets" / filename
        if candidate.exists():
            return candidate
        return base / filename

    base = Path(__file__).resolve().parent.parent
    candidate = base / "assets" / filename
    if candidate.exists():
        return candidate
    return Path(filename)


def extract_packaged_app_icon(aumid_or_path: str, size: int = 16) -> QPixmap | None:
    """Extract lightweight native 16x16 icon for packaged applications."""
    try:
        import ctypes
        import ctypes.wintypes
        from ctypes import POINTER, Structure, WINFUNCTYPE, byref, c_int, c_long, c_void_p, HRESULT

        shell32 = ctypes.windll.shell32
        gdi32 = ctypes.windll.gdi32
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)

        class SIZE(Structure):
            _fields_ = [("cx", c_long), ("cy", c_long)]

        class GUID(Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        IID_IShellItemImageFactory = GUID(
            0xBCC18B79,
            0xBA16,
            0x442F,
            (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B),
        )

        pItem = c_void_p()
        parsing_name = (
            aumid_or_path
            if aumid_or_path.lower().startswith("shell:")
            else f"shell:AppsFolder\\{aumid_or_path}"
        )
        hr = shell32.SHCreateItemFromParsingName(
            parsing_name, None, byref(IID_IShellItemImageFactory), byref(pItem)
        )
        if hr != 0 or not pItem.value:
            return None

        vtable = ctypes.cast(pItem, POINTER(POINTER(c_void_p))).contents
        GetImageProto = WINFUNCTYPE(
            HRESULT, c_void_p, SIZE, c_int, POINTER(ctypes.wintypes.HBITMAP)
        )
        GetImage = GetImageProto(vtable[3])

        hbm = ctypes.wintypes.HBITMAP()
        req_size = SIZE(size, size)
        hr_img = GetImage(pItem, req_size, 0, byref(hbm))

        pixmap = None
        if hr_img == 0 and hbm.value:
            img = QImage.fromHBITMAP(hbm.value)
            if not img.isNull():
                pixmap = QPixmap.fromImage(img)
            gdi32.DeleteObject(hbm)

        return pixmap
    except Exception:
        return None


class IconCache:
    """Minimal icon cache using standard 16x16 icons for low memory usage."""

    def __init__(self, fallback_icon: QIcon):
        self._cache: dict[str, QIcon] = {}
        self._fallback = fallback_icon

    def get_icon(self, app: DiscoveredApp) -> QIcon:
        key = app.app_id
        if key in self._cache:
            return self._cache[key]

        icon = None
        try:
            if app.launch_type == "packaged":
                pix = extract_packaged_app_icon(app.target, 16)
                if pix and not pix.isNull():
                    icon = QIcon(pix)
            else:
                target_path = app.icon_source or app.target
                if target_path and os.path.exists(target_path):
                    from PySide6.QtWidgets import QFileIconProvider
                    qicon = QFileIconProvider().icon(QFileInfo(target_path))
                    if not qicon.isNull():
                        icon = qicon
        except Exception:
            pass

        if not icon or icon.isNull():
            icon = self._fallback

        self._cache[key] = icon
        return icon


class DiscoveryWorker(QThread):
    """Background worker for non-blocking app discovery."""

    apps_discovered = Signal(list)

    def run(self):
        try:
            apps = discover_all_applications(ascending=True)
            self.apps_discovered.emit(apps)
        except Exception as e:
            logger.error("Error during app discovery: %s", e)
            self.apps_discovered.emit([])


def check_is_listener_running() -> bool:
    """Check if listener process is active."""
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name == "omenbuttonlistener.exe":
                return True
            if name in ("python.exe", "pythonw.exe"):
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "listener.py" in cmdline:
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


def get_scheduled_task_state() -> tuple[bool, bool, str]:
    """Query Windows Task Scheduler for task state."""
    try:
        import win32com.client
        svc = win32com.client.Dispatch("Schedule.Service")
        svc.Connect()
        folder = svc.GetFolder("\\")
        task = folder.GetTask(SCHEDULED_TASK_NAME)
        state_map = {0: "Unknown", 1: "Disabled", 2: "Queued", 3: "Ready", 4: "Running"}
        return True, bool(task.Enabled), state_map.get(task.State, f"Code {task.State}")
    except Exception:
        pass

    try:
        ps_cmd = (
            f"Get-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -ErrorAction Stop | "
            f"Select-Object -Property TaskName, State | ConvertTo-Json"
        )
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and "TaskName" in res.stdout:
            is_disabled = "Disabled" in res.stdout
            return True, not is_disabled, "Disabled" if is_disabled else "Ready"
    except Exception:
        pass

    return False, False, "Not Installed"


def find_listener_executable() -> Path | None:
    """Resolve listener binary or script."""
    local_bin = Path(os.path.expandvars(r"%LOCALAPPDATA%\OmenButtonRemapper\bin\OmenButtonListener.exe"))
    if local_bin.is_file():
        return local_bin

    if hasattr(sys, "_MEIPASS"):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent / "dist"

    dist_exe = exe_dir / "OmenButtonListener.exe"
    if dist_exe.is_file():
        return dist_exe

    src_listener = Path(__file__).resolve().parent / "listener.py"
    if src_listener.is_file():
        return src_listener

    return None


def execute_listener_action(action: str) -> tuple[bool, str]:
    """Start, stop, or restart the background listener."""
    if action == "stop":
        try:
            import win32com.client
            svc = win32com.client.Dispatch("Schedule.Service")
            svc.Connect()
            svc.GetFolder("\\").GetTask(SCHEDULED_TASK_NAME).Stop(0)
        except Exception:
            pass

        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info["name"] or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if name == "omenbuttonlistener.exe" or (
                    name in ("python.exe", "pythonw.exe") and "listener.py" in cmdline
                ):
                    proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        time.sleep(0.4)
        if not check_is_listener_running():
            return True, "Service stopped."
        return False, "Could not confirm service stopped."

    elif action == "start":
        if check_is_listener_running():
            return True, "Service is already running."

        task_exists, is_enabled, _ = get_scheduled_task_state()
        if task_exists and is_enabled:
            # Attempt 1: Native COM start (zero console window)
            try:
                import win32com.client
                svc = win32com.client.Dispatch("Schedule.Service")
                svc.Connect()
                svc.GetFolder("\\").GetTask(SCHEDULED_TASK_NAME).Run(None)
                time.sleep(0.5)
                if check_is_listener_running():
                    return True, "Service started via Task Scheduler."
            except Exception:
                pass

            # Attempt 2: PowerShell with CREATE_NO_WINDOW
            try:
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"Start-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}'"],
                    check=False,
                    capture_output=True,
                    timeout=5,
                    creationflags=creationflags,
                )
                time.sleep(0.6)
                if check_is_listener_running():
                    return True, "Service started via Task Scheduler."
            except Exception:
                pass

        listener_path = find_listener_executable()
        if not listener_path:
            return False, "Listener executable not found."

        try:
            cmd = [sys.executable, str(listener_path)] if listener_path.suffix.lower() == ".py" else [str(listener_path)]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(
                cmd,
                cwd=str(listener_path.parent),
                creationflags=creationflags,
            )
            time.sleep(0.6)
            if check_is_listener_running():
                return True, "Service started directly."
            return False, "Service launched but exited. Check listener.log."
        except Exception as e:
            return False, f"Launch failed: {e}"

    elif action == "restart":
        execute_listener_action("stop")
        time.sleep(0.4)
        return execute_listener_action("start")

    return False, f"Unknown action: {action}"


def set_start_with_windows(enable: bool) -> tuple[bool, str]:
    """Configure scheduled task sign-in trigger."""
    # Attempt 1: Native COM toggle (100% silent)
    try:
        import win32com.client
        svc = win32com.client.Dispatch("Schedule.Service")
        svc.Connect()
        task = svc.GetFolder("\\").GetTask(SCHEDULED_TASK_NAME)
        task.Enabled = enable
        time.sleep(0.2)
        _, new_enabled, _ = get_scheduled_task_state()
        if new_enabled == enable:
            return True, f"Auto-start {'enabled' if enable else 'disabled'}."
    except Exception:
        pass

    task_exists, is_enabled, _ = get_scheduled_task_state()

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    if task_exists:
        cmd = f"{'Enable' if enable else 'Disable'}-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}'"
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            _, new_enabled, _ = get_scheduled_task_state()
            if new_enabled == enable:
                return True, f"Auto-start {'enabled' if enable else 'disabled'}."
            return False, "Verification failed."
        return False, res.stderr.strip() or "Command failed."

    else:
        if not enable:
            return True, "Auto-start is already disabled."

        listener_exe = find_listener_executable()
        if not listener_exe or listener_exe.suffix.lower() == ".py":
            return False, "Listener executable not found. Run install script."

        ps_script = f"""
$action = New-ScheduledTaskAction -Execute '{listener_exe}' -WorkingDirectory '{listener_exe.parent}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden
Register-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -ErrorAction Stop
"""
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            task_exists, new_enabled, _ = get_scheduled_task_state()
            if task_exists and new_enabled:
                return True, "Auto-start task created."
            return False, "Task registration verification failed."
        return False, res.stderr.strip() or "Registration failed."


class OmenConfiguratorWindow(QMainWindow):
    """Minimal, traditional Windows service manager style configurator."""

    def __init__(self):
        super().__init__()
        self._all_apps: list[DiscoveredApp] = []
        self._icon_cache = IconCache(
            QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
        )
        self._is_updating_ui = False

        self.setWindowTitle("OMEN Button Service")
        self.setFixedSize(460, 530)

        # Normal minimize and close, no maximize
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        icon_path = get_asset_path("app_icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._load_current_configuration()
        self._start_discovery_worker()
        self._start_status_timer()

    def _build_ui(self):
        """Construct a compact, clean Windows service control interface."""
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # -------------------------------------------------------------
        # Group 1: Service Status & Lifecycle
        # -------------------------------------------------------------
        svc_group = QGroupBox("Listener Service")
        svc_layout = QVBoxLayout(svc_group)
        svc_layout.setContentsMargins(10, 10, 10, 10)
        svc_layout.setSpacing(8)

        # Status row
        status_row = QHBoxLayout()
        status_label = QLabel("Status:")
        status_label.setFixedWidth(50)
        self.status_badge = QLabel("Checking...")
        self.status_badge.setFont(QFont("Segoe UI", 9, QFont.Bold))
        status_row.addWidget(status_label)
        status_row.addWidget(self.status_badge)
        status_row.addStretch()

        self.btn_start = QPushButton("Start")
        self.btn_start.setFixedWidth(65)
        self.btn_start.clicked.connect(lambda: self._handle_lifecycle_action("start"))

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setFixedWidth(65)
        self.btn_stop.clicked.connect(lambda: self._handle_lifecycle_action("stop"))

        self.btn_restart = QPushButton("Restart")
        self.btn_restart.setFixedWidth(65)
        self.btn_restart.clicked.connect(lambda: self._handle_lifecycle_action("restart"))

        status_row.addWidget(self.btn_start)
        status_row.addWidget(self.btn_stop)
        status_row.addWidget(self.btn_restart)
        svc_layout.addLayout(status_row)

        # Auto-start checkbox & feedback row
        startup_row = QHBoxLayout()
        self.startup_checkbox = QCheckBox("Start service automatically on Windows sign-in")
        self.startup_checkbox.clicked.connect(self._on_startup_toggled)
        self.svc_feedback = QLabel("")
        self.svc_feedback.setStyleSheet("color: #555555; font-size: 11px;")

        startup_row.addWidget(self.startup_checkbox)
        startup_row.addStretch()
        startup_row.addWidget(self.svc_feedback)
        svc_layout.addLayout(startup_row)

        main_layout.addWidget(svc_group)

        # -------------------------------------------------------------
        # Group 2: Key Mapping Selection
        # -------------------------------------------------------------
        map_group = QGroupBox("Key Mapping")
        map_layout = QVBoxLayout(map_group)
        map_layout.setContentsMargins(10, 10, 10, 10)
        map_layout.setSpacing(6)

        # Current target readout
        active_row = QHBoxLayout()
        active_title = QLabel("Active Target:")
        active_title.setFixedWidth(80)
        self.active_app_label = QLabel("None")
        self.active_app_label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        active_row.addWidget(active_title)
        active_row.addWidget(self.active_app_label)
        active_row.addStretch()
        map_layout.addLayout(active_row)

        # Search & Sort Controls
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter applications...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)

        self.sort_combo = QComboBox()
        self.sort_combo.addItem("A-Z", True)
        self.sort_combo.addItem("Z-A", False)
        self.sort_combo.setFixedWidth(65)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)

        filter_row.addWidget(self.search_input)
        filter_row.addWidget(self.sort_combo)
        map_layout.addLayout(filter_row)

        # Compact App List (16x16 icons, 24px rows for high efficiency)
        self.app_list = QListWidget()
        self.app_list.setIconSize(QSize(16, 16))
        self.app_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.app_list.setStyleSheet("QListWidget::item { height: 24px; }")
        self.app_list.itemSelectionChanged.connect(self._on_item_selection_changed)
        map_layout.addWidget(self.app_list, stretch=1)

        # Save Button and Status
        action_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Selection")
        self.save_btn.setDefault(True)
        self.save_btn.setFixedWidth(110)
        self.save_btn.clicked.connect(self._on_save_clicked)

        self.map_feedback = QLabel("")
        self.map_feedback.setStyleSheet("font-size: 11px;")

        action_row.addWidget(self.save_btn)
        action_row.addWidget(self.map_feedback, stretch=1)
        map_layout.addLayout(action_row)

        main_layout.addWidget(map_group, stretch=1)

    def _load_current_configuration(self):
        """Display currently active configured target."""
        config = load_config()
        selected = config.get("selected_app")
        if selected and isinstance(selected, dict):
            name = selected.get("name", "Unknown")
            self.active_app_label.setText(name)
        else:
            self.active_app_label.setText("None (Unmapped)")

    def _start_discovery_worker(self):
        """Discover applications in background thread."""
        loading_item = QListWidgetItem("Scanning applications...")
        loading_item.setFlags(Qt.NoItemFlags)
        self.app_list.addItem(loading_item)

        self._worker = DiscoveryWorker()
        self._worker.apps_discovered.connect(self._on_apps_discovered)
        self._worker.start()

    def _on_apps_discovered(self, apps: list[DiscoveredApp]):
        """Populate list widget once background scan completes."""
        self._all_apps = apps
        self._populate_application_list()

    def _populate_application_list(self):
        """Populate list with minimal 16x16 items."""
        self.app_list.clear()

        config = load_config()
        current_target = None
        if config.get("selected_app"):
            current_target = config["selected_app"].get("target", "").strip().lower()

        matched_item = None
        for app in self._all_apps:
            item = QListWidgetItem(f" {app.display_name}")
            item.setData(Qt.UserRole, app)
            item.setToolTip(f"{app.display_name}\nTarget: {app.target}")
            item.setIcon(self._icon_cache.get_icon(app))
            self.app_list.addItem(item)

            if current_target and app.target.strip().lower() == current_target:
                matched_item = item

        if matched_item:
            self.app_list.setCurrentItem(matched_item)
            self.app_list.scrollToItem(matched_item)

        self._on_search_text_changed(self.search_input.text())

    def _on_search_text_changed(self, text: str):
        """Filter list without modifying items."""
        query = text.strip().lower()
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            app = item.data(Qt.UserRole)
            if app:
                item.setHidden(query not in app.display_name.lower())

    def _on_sort_changed(self):
        """Sort list ascending or descending."""
        ascending = self.sort_combo.currentData()
        self._all_apps = sort_applications(self._all_apps, ascending=ascending)
        self._populate_application_list()

    def _on_item_selection_changed(self):
        """Update status on click."""
        selected_items = self.app_list.selectedItems()
        if selected_items:
            app: DiscoveredApp = selected_items[0].data(Qt.UserRole)
            if app:
                self.map_feedback.setText(f"Selected: {app.display_name}")
                self.map_feedback.setStyleSheet("color: #333333;")

    def _on_save_clicked(self):
        """Validate and atomically save selected application."""
        selected_items = self.app_list.selectedItems()
        if not selected_items:
            self.map_feedback.setText("Select an app from the list.")
            self.map_feedback.setStyleSheet("color: #C00000; font-weight: bold;")
            return

        app: DiscoveredApp = selected_items[0].data(Qt.UserRole)
        app_dict = app.to_config_dict()

        is_valid, val_msg = validate_target(app_dict)
        if not is_valid:
            self.map_feedback.setText(f"Error: {val_msg}")
            self.map_feedback.setStyleSheet("color: #C00000; font-weight: bold;")
            return

        new_config = {"version": 1, "selected_app": app_dict}

        try:
            save_config(new_config)
            self._load_current_configuration()
            self.map_feedback.setText(f"Saved: {app.display_name}")
            self.map_feedback.setStyleSheet("color: #008000; font-weight: bold;")
        except Exception as e:
            self.map_feedback.setText(f"Save failed: {e}")
            self.map_feedback.setStyleSheet("color: #C00000; font-weight: bold;")

    def _start_status_timer(self):
        """Poll service and task state every 2.5 seconds."""
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_listener_status)
        self._status_timer.start(2500)
        self._refresh_listener_status()

    def _refresh_listener_status(self):
        """Query real process and task status and update controls."""
        is_running = check_is_listener_running()
        if is_running:
            self.status_badge.setText("Running")
            self.status_badge.setStyleSheet("color: #008000; font-weight: bold;")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_restart.setEnabled(True)
        else:
            self.status_badge.setText("Stopped")
            self.status_badge.setStyleSheet("color: #C00000; font-weight: bold;")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_restart.setEnabled(False)

        task_exists, is_enabled, _ = get_scheduled_task_state()
        self._is_updating_ui = True
        try:
            self.startup_checkbox.setChecked(task_exists and is_enabled)
        finally:
            self._is_updating_ui = False

    def _handle_lifecycle_action(self, action: str):
        """Handle Start, Stop, or Restart buttons."""
        self.svc_feedback.setText(f"{action.capitalize()}ing...")
        QApplication.processEvents()

        success, msg = execute_listener_action(action)
        self._refresh_listener_status()
        self.svc_feedback.setText(msg)
        self.svc_feedback.setStyleSheet("color: #008000;" if success else "color: #C00000;")

    def _on_startup_toggled(self, checked: bool):
        """Handle auto-start checkbox click."""
        if self._is_updating_ui:
            return

        self.svc_feedback.setText("Updating Task Scheduler...")
        QApplication.processEvents()

        success, msg = set_start_with_windows(checked)
        self._refresh_listener_status()
        self.svc_feedback.setText(msg)
        self.svc_feedback.setStyleSheet("color: #008000;" if success else "color: #C00000;")


def main():
    """Application entrypoint for OmenButtonConfigurator."""
    app = QApplication(sys.argv)
    app.setApplicationName("OmenButtonConfigurator")
    app.setOrganizationName("OmenButtonRemapper")

    # Native Windows Vista / modern platform style for minimal RAM
    app.setStyle("windowsvista")

    window = OmenConfiguratorWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
