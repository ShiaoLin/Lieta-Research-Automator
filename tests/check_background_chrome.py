"""Opt-in Lieta UI regression check. Requires the app's logged-in Chrome.

Run from the project root: `python tests/check_background_chrome.py`.
This check selects models and types tickers; it never submits model requests.
"""

import argparse
import ctypes
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lieta_automator import chrome_launcher
from lieta_automator.scraper import LietaScraper


def foreground_window():
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    return user32.GetForegroundWindow()


def foreground_pid():
    pid = ctypes.c_ulong()
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId(foreground_window(), ctypes.byref(pid))
    return pid.value


def restore_without_activation(driver, pid):
    """CDP's normal-window restore activates Chrome; Win32 SW_SHOWNOACTIVATE does not."""
    user32 = ctypes.windll.user32
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    title = driver.title
    windows = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    def collect(window, unused):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid:
            text = ctypes.create_unicode_buffer(1024)
            user32.GetWindowTextW(window, text, len(text))
            if title and title in text.value:
                windows.append(window)
        return 1

    user32.EnumWindows(collect, 0)
    if len(windows) != 1:
        raise RuntimeError("Cannot uniquely identify the test Chrome window")
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow(windows[0], 4)  # SW_SHOWNOACTIVATE


def browser_pid(port):
    output = subprocess.check_output(["netstat", "-aon", "-p", "TCP"], text=True)
    match = re.search(r"TCP\s+127\.0\.0\.1:" + str(port) + r"\s+.*?LISTENING\s+(\d+)", output)
    if not match:
        raise RuntimeError(f"Cannot identify Chrome listening on port {port}")
    return int(match.group(1))


def snapshot(driver, pid):
    state = driver.execute_script("""
        const model = document.querySelector('button[role="combobox"]');
        const input = document.querySelector('input[placeholder="Ticker"]');
        return {
          visibility: document.visibilityState,
          model: model ? model.innerText.trim() : null,
          ticker: input ? input.value : null,
          animations: document.getAnimations().map(a => ({state:a.playState, time:a.currentTime}))
        };
    """)
    state["window_state"] = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})["bounds"]["windowState"]
    state["browser_in_foreground"] = foreground_pid() == pid
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--profile-dir", help="Optional app profile to launch if its Chrome is closed")
    parser.add_argument("--output", default=".test-output/background-chrome.json")
    parser.add_argument("--states", nargs="+", choices=["minimized", "normal"], default=["minimized", "normal"])
    parser.add_argument("--skip-control", action="store_true", help="Only recheck selected positive cases")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"date": datetime.now().isoformat(timespec="seconds"), "checks": []}
    scraper = LietaScraper(str(output.parent / "unused-downloads"), args.port)
    window = None
    try:
        if args.profile_dir:
            if not chrome_launcher.launch_chrome_in_debug_mode(args.port, args.profile_dir):
                raise RuntimeError("Cannot launch Chrome")
        if not chrome_launcher.wait_for_chrome(args.port) or not scraper.setup_driver():
            raise RuntimeError("Cannot attach to the app's Chrome")
        driver = scraper.driver
        pid = browser_pid(args.port)
        report["chrome"] = driver.capabilities.get("browserVersion")
        report["chromedriver"] = driver.capabilities.get("chrome", {}).get("chromedriverVersion", "").split(" ")[0]
        window = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
        if not scraper.check_login_status():
            raise RuntimeError("The app's Chrome needs an active Lieta login")

        # Control: the same minimized page with focus emulation disabled.
        driver.minimize_window()
        if not args.skip_control:
            driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": False})
            report["control_before"] = snapshot(driver, pid)
            try:
                scraper._select_model("Gamma")
                scraper._fill_ticker("SPY")
                report["control_reproduced"] = False
            except Exception as exc:
                report["control_reproduced"] = True
                report["control_error"] = type(exc).__name__ + ": " + str(exc).split("\n")[0]
            report["control_after"] = snapshot(driver, pid)
            print("Control reproduced:", report["control_reproduced"], flush=True)

        driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
        for state in args.states:
            if state == "normal":
                restore_without_activation(driver, pid)
            else:
                driver.minimize_window()
            # Record physical foreground state separately from emulated visibility.
            for model in ("Gamma", "Term", "Smile", "TV Code"):
                before = snapshot(driver, pid)
                started = time.monotonic()
                scraper._select_model(model)
                scraper._fill_ticker("SPY")
                after = snapshot(driver, pid)
                result = {"requested_window_state": state, "model": model,
                          "elapsed_seconds": round(time.monotonic() - started, 2),
                          "before": before, "after": after}
                result["passed"] = (after["model"] == model and after["ticker"] == "SPY"
                                    and after["window_state"] == state
                                    and not before["browser_in_foreground"]
                                    and not after["browser_in_foreground"])
                report["checks"].append(result)
                print(state, model, "PASS" if result["passed"] else "FAIL", flush=True)
        report["passed"] = report.get("control_reproduced", True) and all(check["passed"] for check in report["checks"])
        return 0 if report["passed"] else 1
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        report["passed"] = False
        print(report["error"], flush=True)
        return 2
    finally:
        if window and scraper.driver:
            try:
                if window["bounds"]["windowState"] == "normal":
                    restore_without_activation(scraper.driver, pid)
                else:
                    scraper.driver.execute_cdp_cmd("Browser.setWindowBounds", {
                        "windowId": window["windowId"],
                        "bounds": {"windowState": window["bounds"]["windowState"]},
                    })
            except Exception:
                pass
        scraper.close_driver()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Report:", output.resolve(), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
