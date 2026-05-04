"""Launch Chromium with remote debugging so the AA MCP can extract session cookies after login.

Usage: `aa-auth-browser` (console script). Cross-platform: macOS, Linux, Windows.

The browser uses a persistent profile dir per account, so cookies survive between launches.
After logging into aa.com once, run `aa-mcp-server --extract-session <account>` (or call the
`save_session_from_browser` MCP tool) to copy the cookies into the MCP's account store.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_PORT = 9224
BASE_DIR = Path.home() / ".aa-mcp"


def _profile_dir(account: str) -> Path:
    return BASE_DIR / "chrome-profiles" / account


def find_chromium() -> str | None:
    if env := os.environ.get("AA_MCP_CHROMIUM"):
        if Path(env).exists():
            return env

    home = Path.home()
    candidates: list[Path] = [
        home / "Downloads" / "chrome-win" / "chrome-win" / "chrome.exe",
        home / "Downloads" / "chrome-win" / "chrome.exe",
    ]

    system = platform.system()
    if system == "Darwin":
        candidates += [
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    elif system == "Windows":
        for env_var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            if base := os.environ.get(env_var):
                candidates += [
                    Path(base) / "Chromium" / "Application" / "chrome.exe",
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                ]

    for path in candidates:
        if path.exists():
            return str(path)

    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if path := shutil.which(name):
            return path

    return None


def main() -> int:
    account = "default"
    port = int(os.environ.get("AA_AUTH_PORT", DEFAULT_PORT))
    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        account = args[0]

    profile = _profile_dir(account)
    profile.mkdir(parents=True, exist_ok=True)

    chromium = find_chromium()
    if chromium is None:
        print(
            "Chromium/Chrome not found. Set AA_MCP_CHROMIUM=<path>, or install Chromium "
            "(https://download-chromium.appspot.com/) or Google Chrome.",
            file=sys.stderr,
        )
        return 1

    print(f"Launching {chromium}")
    print(f"  --remote-debugging-port={port}")
    print(f"  --user-data-dir={profile}")
    print()
    print(f"Log into aa.com in the browser window (account profile '{account}'),")
    print("then call save_session_from_browser to copy cookies into the MCP store.")
    print()

    return subprocess.call([
        chromium,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.aa.com/loyalty/login",
    ])


if __name__ == "__main__":
    sys.exit(main())
