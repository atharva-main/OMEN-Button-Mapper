"""Automated release publisher for GitHub Releases using standard library."""

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse
from pathlib import Path


def get_github_token() -> str:
    """Retrieve GitHub token from environment or Git Credential Manager."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True,
            text=True,
            check=True,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass

    raise RuntimeError("No GitHub token found in GITHUB_TOKEN env or Git Credential Manager.")


TOKEN = get_github_token()
OWNER = "atharva-main"
REPO = "OMEN-Button-Mapper"
TAG = "v1.0.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
ZIP_PATH = DIST_DIR / "OMEN-Button-Mapper-v1.0.0.zip"
CFG_PATH = DIST_DIR / "OmenButtonConfigurator.exe"
LST_PATH = DIST_DIR / "OmenButtonListener.exe"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def api_request(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None) -> dict | list:
    all_headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "OMEN-Release-Publisher",
    }
    if headers:
        all_headers.update(headers)

    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    if not ZIP_PATH.exists():
        print(f"Error: {ZIP_PATH} does not exist. Please build first.", file=sys.stderr)
        sys.exit(1)

    zip_hash = sha256_file(ZIP_PATH)
    cfg_hash = sha256_file(CFG_PATH)
    lst_hash = sha256_file(LST_PATH)

    body = f"""## OMEN Button Remapper for Windows (v1.0.0)

A lightweight Windows utility that remaps the dedicated **OMEN** key on HP Victus and HP OMEN laptops to launch or focus any desktop or Microsoft Store application.

### Key Highlights:
- **Portable & Ready-to-Use**: No Python or compilation tools required. Just download, extract, and run.
- **Built-in Hardware & Key Diagnostics**: Real-time ACPI WMI driver verification (`root\\wmi:hpqBEvnt`), HP software conflict scanner (OMEN Gaming Hub, HP System Event Utility), and live physical key-press detection.
- **Smart Window Activation**: If your configured application (e.g. an open Excel spreadsheet) is already open, pressing the OMEN key brings it straight to the foreground instead of spawning duplicate windows.
- **Silent Background Operation**: Uses native Windows Task Scheduler in hidden mode with zero console flash on startup or Windows sign-in.
- **Minimal RAM Footprint**: Traditional Windows service applet interface with low memory consumption (~20 MB).

---

### Quick Start:
1. Download **`OMEN-Button-Mapper-v1.0.0.zip`** below and extract it anywhere on your PC.
2. Double-click **`OmenButtonConfigurator.exe`**.
   > **Note on Windows SmartScreen**: Because open-source tools do not purchase costly enterprise certificates, Windows may show *"Windows protected your PC"*. Click **More info** -> **Run anyway**.
3. The Hardware Diagnostics Assistant will verify your laptop's HP ACPI driver and let you test the physical key.
4. Select your application from the list, click **Save Selection**, enable **Start service automatically on Windows sign-in**, and click **Start**.

---

### File Integrity (SHA-256 Checksums):
- **OMEN-Button-Mapper-v1.0.0.zip**: `{zip_hash}`
- **OmenButtonConfigurator.exe**: `{cfg_hash}`
- **OmenButtonListener.exe**: `{lst_hash}`
"""

    print(f"Checking for existing release for tag {TAG}...")
    release = None
    try:
        release = api_request(f"https://api.github.com/repos/{OWNER}/{REPO}/releases/tags/{TAG}")
        print(f"Found existing release ID {release['id']}. Updating...")
        payload = json.dumps({"name": "OMEN Button Remapper v1.0.0", "body": body}).encode("utf-8")
        release = api_request(
            f"https://api.github.com/repos/{OWNER}/{REPO}/releases/{release['id']}",
            method="PATCH",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("Creating new release...")
            payload = json.dumps({
                "tag_name": TAG,
                "target_commitish": "main",
                "name": "OMEN Button Remapper v1.0.0",
                "body": body,
                "draft": False,
                "prerelease": False,
            }).encode("utf-8")
            release = api_request(
                f"https://api.github.com/repos/{OWNER}/{REPO}/releases",
                method="POST",
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        else:
            raise

    print(f"[OK] Release published at: {release['html_url']}")

    # Clean existing asset if present
    assets = api_request(release["assets_url"])
    for asset in assets:
        if asset["name"] == ZIP_PATH.name:
            print(f"Deleting older asset '{asset['name']}'...")
            req = urllib.request.Request(
                asset["url"],
                headers={
                    "Authorization": f"token {TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "OMEN-Release-Publisher",
                },
                method="DELETE",
            )
            with urllib.request.urlopen(req):
                pass

    # Upload zip asset
    print(f"Uploading {ZIP_PATH.name} ({ZIP_PATH.stat().st_size / (1024*1024):.2f} MB)...")
    upload_url_base = release["upload_url"].split("{")[0]
    upload_url = f"{upload_url_base}?name={urllib.parse.quote(ZIP_PATH.name)}"

    with open(ZIP_PATH, "rb") as f:
        file_bytes = f.read()

    req = urllib.request.Request(
        upload_url,
        data=file_bytes,
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/zip",
            "User-Agent": "OMEN-Release-Publisher",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        asset_info = json.loads(resp.read().decode("utf-8"))

    print(f"[OK] Asset uploaded successfully!")
    print(f"Download URL: {asset_info['browser_download_url']}")


if __name__ == "__main__":
    main()
