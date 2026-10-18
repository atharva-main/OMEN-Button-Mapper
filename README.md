# OMEN Button Remapper for Windows

A lightweight Windows utility that remaps the dedicated **OMEN** key on HP Victus and OMEN laptops (such as the HP Victus 15) to launch any desktop or Microsoft Store application of your choice.

---

## 1. Project Overview

The project provides two independent executables:

1. **`OmenButtonConfigurator.exe`**: A native PySide6 GUI to discover installed applications, select a remapping target, save settings atomically, and control background listener startup and lifecycle.
2. **`OmenButtonListener.exe`**: A standalone, lightweight background process that detects the OMEN hardware button event via Windows WMI and launches the configured application.

### Key Characteristics
- **Lightweight Background Footprint**: The listener runs silently with zero console windows, consuming minimal CPU/RAM while waiting on WMI notifications. It does not load Qt/GUI libraries.
- **Smart Window Activation**: If the selected application (e.g. an open Excel workbook or browser) is already running, pressing the OMEN button brings it directly to the foreground instead of spawning duplicate instances. Newly launched applications are guaranteed to appear on top of other windows.
- **Zero Console Flash**: Task Scheduler tasks and process lifecycle controls use native COM `Schedule.Service` and `CREATE_NO_WINDOW`, ensuring no console or command prompt window briefly flickers on startup, restart, or Windows sign-in.
- **Minimal Service Applet UI**: The configurator features a traditional, minimal Windows service applet design with low memory consumption (~20 MB RAM), responsive filtering, and native system styling.
- **Dynamic Configuration**: The listener re-reads the JSON configuration on every button press. Changes made in the GUI take effect immediately without needing to restart the background listener.
- **Hardware & Key Diagnostics Wizard**: Built-in interactive assistant that verifies HP ACPI WMI drivers (`root\wmi:hpqBEvnt`), checks for conflicting HP background bloatware (OMEN Gaming Hub, HP System Event Utility), and provides live physical button detection.
- **Session-Aware**: Operates entirely in the logged-in user's interactive desktop session using Windows Task Scheduler (avoiding Session 0 isolation issues).
- **Zero Admin Rights Required**: Normal operation, configuration, and scheduled task registration run under the standard user account without requiring administrator privileges.
- **Hardware-Safe**: Does not alter BIOS, drivers, firmware, or system services.

---

## 2. Quick Download & Setup (For End Users)

No Python or build tools required.

1. **Download**: Go to [Releases](https://github.com/atharva-main/OMEN-Button-Mapper/releases) and download `OMEN-Button-Mapper-v1.0.0.zip`.
2. **Extract**: Extract the zip contents to any convenient folder on your machine.
3. **Run**: Double-click `OmenButtonConfigurator.exe`.
   > **Note on Windows SmartScreen**: Because open-source tools do not purchase costly enterprise code-signing certificates, Windows may show *"Windows protected your PC"*. Simply click **More info** &rarr; **Run anyway**.
4. **Diagnostics**: The built-in assistant will verify your laptop's HP ACPI driver and let you test the physical key.
5. **Remap**: Select your desired application from the list, click **Save Selection**, enable **Start service automatically on Windows sign-in**, and click **Start**.

### Requirements for Best Experience
- **Uninstall or Close OMEN Gaming Hub**: Prevents HP's software from fighting for the key.
- **Disable HP System Event Utility**: In Task Manager &rarr; Startup Apps if on-screen notifications appear.
- **Keep HP System Drivers**: Keep *HP Application Enabling Services* installed in Device Manager (required for hardware key events).

---

## 3. Architecture and Project Structure

```text
OMEN Button Mapper/
├── src/
│   ├── configurator.py       # PySide6 GUI Configurator, diagnostics wizard & service controls
│   ├── listener.py           # Background WMI event listener & single-instance daemon
│   ├── compatibility.py      # Hardware inspection, conflict detection & live key listener
│   ├── app_discovery.py      # Start Menu (.lnk) and Microsoft Store (AUMID) discovery
│   └── app_launcher.py       # Launch execution, window foreground activation & atomic config
├── assets/
│   └── app_icon.ico          # Application multi-resolution icon
├── config/
│   └── config.example.json   # Schema example configuration
├── scripts/
│   ├── build.ps1             # PyInstaller automated build script
│   ├── install_listener.ps1  # User-level scheduled task & binary installer
│   └── uninstall_listener.ps1# Task unregistration & cleanup script
├── tests/
│   ├── test_config.py        # Schema validation, atomic write & recovery tests
│   ├── test_app_discovery.py # Discovery model, deduplication & sorting tests
│   ├── test_app_launcher.py  # Executable resolution, target validation & launch tests
│   ├── test_listener.py      # Event filtering, debounce & mutex tests
│   └── test_lifecycle.py     # Lifecycle & scheduled task status tests
├── requirements.txt          # Python dependencies
├── pytest.ini                # Test runner configuration
├── README.md                 # Full documentation and manual checklist
└── .gitignore                # Source control ignore rules
```

---

## 4. Development Setup

1. **Clone or open the repository**:
   ```powershell
   cd "D:\programming\python\OMEN Button Mapper"
   ```

2. **Install dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```

3. **Run tests**:
   ```powershell
   pytest -v
   ```

4. **Run components directly from source**:
   - To launch the GUI Configurator:
     ```powershell
     python src/configurator.py
     ```
   - To run the Listener in debug mode (prints events to console):
     ```powershell
     python src/listener.py --debug
     ```

---

## 5. Building the Executables

The build script uses PyInstaller to produce two independent executables:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Clean
```

Outputs in `dist/`:
- `dist\OmenButtonConfigurator.exe`: Windowed GUI with bundled icons.
- `dist\OmenButtonListener.exe`: Lightweight, noconsole background process.

---

## 6. Installation & Automatic Startup

### Automatic Installation
Run the installation script in PowerShell (no administrator privileges needed):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_listener.ps1
```

What the installer does:
1. Creates `%LOCALAPPDATA%\OmenButtonRemapper` with `bin/` and `logs/` directories.
2. Copies `OmenButtonListener.exe` (and `OmenButtonConfigurator.exe`) into `bin/`.
3. Preserves any existing `config.json`, or creates a clean default configuration if missing.
4. Registers the scheduled task `OmenButtonRemapperListener` configured to:
   - Trigger at current user sign-in (`-AtLogOn -User $env:USERNAME`).
   - Run with standard user privileges in the interactive user session.
   - Run on battery power without being terminated by power events.
   - Ignore new instances if already running.
5. Starts the listener process.

### Uninstallation
To stop the listener, remove the scheduled task, and delete the installed binaries:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall_listener.ps1
```

- By default, your saved `config.json` and logs are preserved.
- To wipe configuration and logs as well, pass the switch:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\uninstall_listener.ps1 -RemoveConfig
  ```

---

## 7. How the Background Listener Works

1. **Single-Instance Enforcement**: On startup, `OmenButtonListener` attempts to acquire a named Windows Mutex (`Local\OmenButtonRemapperListenerMutex`). If another instance is running, it exits cleanly.
2. **WMI Event Query**: Subscribes to the HP WMI event class using:
   ```sql
   SELECT * FROM hpqBEvnt WHERE EventID = 29 AND EventData = 8613
   ```
   via `ExecNotificationQuery` in `root\wmi`.
3. **Low-Power Waiting**: Uses a blocking `NextEvent(1000)` call. When no event occurs, it times out without consuming CPU cycles and checks whether a stop signal was requested.
4. **Debounce Logic**: Hardware key presses may deliver electrical bounce or duplicate events in rapid succession. The listener debounces events within 400 milliseconds, ensuring only one application launch occurs per physical press.
5. **Dynamic Configuration Reading**: When a button press is verified, the listener reads `%LOCALAPPDATA%\OmenButtonRemapper\config.json`.
6. **Safe Execution**:
   - **Executables**: Resolves paths via filesystem, `PATH`, and Windows Registry `App Paths` (`excel.exe`, `notepad.exe`, etc.) and launches them with arguments using `subprocess.Popen` (without `shell=True`).
   - **Shortcuts**: Launches resolved targets or passes `.lnk` paths to the Windows shell.
   - **Packaged Apps (AUMID)**: Activates modern Store apps via `shell:AppsFolder\<AUMID>`.
7. **Resilience & Self-Healing**: If the WMI service drops or an unexpected error occurs, the listener logs the event and automatically re-subscribes after a 5-second backoff.

---

## 8. Configuration Location and Format

Configuration is stored per-user at:

`%LOCALAPPDATA%\OmenButtonRemapper\config.json`

### Schema Example:
```json
{
  "version": 1,
  "selected_app": {
    "name": "Microsoft Excel",
    "launch_type": "executable",
    "target": "excel.exe",
    "arguments": [],
    "working_directory": null
  }
}
```

### Packaged Store App Example:
```json
{
  "version": 1,
  "selected_app": {
    "name": "Calculator",
    "launch_type": "packaged",
    "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
    "arguments": [],
    "working_directory": null
  }
}
```

### Atomic Writes
When saving, the configurator writes to a temporary file in `%LOCALAPPDATA%\OmenButtonRemapper\`, flushes and syncs to disk, and performs an atomic replace (`os.replace`). If writing or schema validation fails, the original configuration remains intact.

---

## 9. Diagnostics and Troubleshooting

### Diagnostic Logs
Logs are automatically rotated (max 1 MB with 3 backup files) and stored at:

`%LOCALAPPDATA%\OmenButtonRemapper\logs\listener.log`

### Testing WMI Detection in Real-Time
To verify that your physical OMEN key generates the expected event:
1. Open PowerShell and run:
   ```powershell
   python src/listener.py --debug
   ```
2. Press the OMEN key on your keyboard.
3. You should see:
   ```text
   OMEN button event verified (EventID=29, EventData=8613)
   Executing remapped action for '...'
   Launch succeeded: Launched ... successfully.
   ```

### Common Issues
- **Button press does nothing**: Check `%LOCALAPPDATA%\OmenButtonRemapper\logs\listener.log`. Ensure an application has been selected and saved in the Configurator.
- **Listener not running**: Open `OmenButtonConfigurator.exe`, inspect the status badge, and click **Start**.
- **Task Scheduler shows Disabled**: In `OmenButtonConfigurator.exe`, check the **Start with Windows** box to re-enable the sign-in trigger.

---

## 10. Manual Verification Checklist

Follow this checklist to verify full functionality:

- [ ] **1. Run Configurator**: Launch `OmenButtonConfigurator.exe` or `python src/configurator.py`. Window appears with fixed dimensions, no maximize button, clean Segoe UI styling, and active target status.
- [ ] **2. Discover Applications**: Verify that the application list is populated with desktop shortcuts and Microsoft Store apps, including their native icons.
- [ ] **3. Search and Sort**: Type in the search box to filter apps in real time without UI freezing. Test sorting A-to-Z and Z-to-A.
- [ ] **4. Select and Save Target**: Select an application (e.g. *Microsoft Excel* or *Notepad*). Click **Save Selection**. Verify success message and updated Active Target label. Verify the app is NOT launched upon saving.
- [ ] **5. Launch via OMEN Button**: Ensure the listener is running. Press the physical OMEN button on the HP Victus keyboard. Verify that the configured application launches immediately.
- [ ] **6. Dynamic Config Change**: With the listener still running, open the Configurator, select a different app (e.g. *Calculator*), and click **Save Selection**. Press the OMEN button again. Verify the new app launches without restarting the listener.
- [ ] **7. Lifecycle Controls**: In the Configurator, click **Stop**. Verify status changes to "Stopped" and process exits. Click **Start**, verify status changes to "Running".
- [ ] **8. Start with Windows Toggle**: Toggle **Start with Windows** off and on. Verify in Task Scheduler (`taskschd.msc`) that `OmenButtonRemapperListener` reflects the state.
- [ ] **9. Sign-out and Sign-in**: Sign out of Windows and sign back in. Verify that `OmenButtonListener.exe` is running in Task Manager without any console window popping up.
- [ ] **10. Safe Uninstallation**: Run `scripts\uninstall_listener.ps1`. Verify the scheduled task is removed and listener process stopped, while `%LOCALAPPDATA%\OmenButtonRemapper\config.json` is preserved.

---

## 11. Known Limitations

1. **HP OMEN / Victus Specific**: The listener relies on the HP WMI ACPI driver exposing the `hpqBEvnt` class. Non-HP hardware or HP models with non-standard BIOS events will not trigger this specific WMI event.
2. **User Session Scope**: Designed to launch desktop applications in the active interactive user session. If multiple users are simultaneously logged into Windows Fast User Switching, the listener runs per active session.
