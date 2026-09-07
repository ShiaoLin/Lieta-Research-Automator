import os
import re
import socket
import subprocess
import winreg
import shutil
import time
import json
from urllib.request import urlopen
from . import config
from .logger import logger

def _copy_tree_ignore_locked(src: str, dst: str):
    """
    Recursively copies a directory tree from src to dst, silently skipping
    any files that are locked by another process (e.g., Chrome's Cookies file).
    Returns a list of files that could not be copied.
    """
    skipped = []
    os.makedirs(dst, exist_ok=True)
    for item in os.scandir(src):
        s = item.path
        d = os.path.join(dst, item.name)
        if item.is_dir():
            child_skipped = _copy_tree_ignore_locked(s, d)
            skipped.extend(child_skipped)
        else:
            try:
                shutil.copy2(s, d)
            except OSError:
                skipped.append(s)
    return skipped


def find_chrome_executable():
    """
    Finds the path to chrome.exe by checking the Windows Registry.
    Returns the path as a string or None if not found.
    """
    try:
        for root_key in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
            try:
                reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                with winreg.OpenKey(root_key, reg_path) as key:
                    chrome_path, _ = winreg.QueryValueEx(key, None)
                    if os.path.exists(chrome_path):
                        return chrome_path
            except FileNotFoundError:
                continue
    except Exception as e:
        logger.error(f"在登錄檔中尋找 Chrome 時發生錯誤: {e}", exc_info=True)
        return None
    return None

def is_port_in_use(port):
    """
    Checks if a given TCP port is already in use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            # Try to bind to the port. If it fails, the port is in use.
            s.bind(("127.0.0.1", port))
            return False
        except socket.error:
            return True

def launch_chrome_in_debug_mode(port: int, user_data_dir: str):
    """
    Ensures a Chrome instance is running in debug mode on a specific port
    with a specific user data directory.
    If the port is not in use, it launches a new Chrome instance.
    Profile syncing must be done before calling this (via sync_all_secondary_profiles).
    """
    logger.info(f"正在檢查 Port {port}...")
    if is_port_in_use(port):
        if owns_debug_port(port, user_data_dir):
            return True
        logger.error(f"Port {port} 被其他程序或 Profile 使用；不會關閉該程序。")
        return False

    logger.info(f"Port {port} 未被使用，正在尋找 Chrome 安裝路徑...")
    chrome_path = find_chrome_executable()
    if not chrome_path:
        logger.error("找不到 Chrome 安裝路徑。請確認已安裝 Chrome。")
        return False
    logger.info(f"正在為 Port {port} 啟動新的 Chrome 偵錯實例...")
    command = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f'--user-data-dir={user_data_dir}',
        config.LIETA_PLATFORM_URL
    ]
    try:
        # Join the command list into a single string to be executed by the shell.
        # This is safer for paths with spaces.
        subprocess.Popen(command)
        logger.info(f"已成功為 Port {port} 啟動 Chrome。請稍候瀏覽器開啟...")
        return True
    except Exception as e:
        logger.error(f"無法為 Port {port} 自動啟動 Chrome: {e}", exc_info=True)
        return False


def wait_for_chrome(port, timeout=20):
    """Wait for Chrome startup before attaching Selenium in unattended mode."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                value = json.load(response)
                if value.get("webSocketDebuggerUrl") and "Chrome" in value.get("Browser", ""):
                    return True
        except (OSError, ValueError):
            time.sleep(0.25)
    return False


def owns_debug_port(port, profile):
    """Validate the listener PID and Chrome profile without terminating anything."""
    script = (f"$listener = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue; "
              "$listener | ForEach-Object { Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess) } "
              "| Select-Object Name,CommandLine | ConvertTo-Json -Compress")
    try:
        output = subprocess.check_output(["powershell", "-NoProfile", "-Command", script],
                                         text=True, encoding="utf-8", errors="replace",
                                         creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        processes = json.loads(output)
        if isinstance(processes, dict):
            processes = [processes]
        expected = os.path.normcase(os.path.abspath(profile))
        for process in processes or []:
            command = process.get("CommandLine") or ""
            match = re.search(r'--user-data-dir=(?:"([^"]+)"|([^\s]+))', command)
            if (process.get("Name", "").lower() == "chrome.exe" and match
                    and os.path.normcase(os.path.abspath(match.group(1) or match.group(2))) == expected):
                return True
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.exception("無法確認偵錯埠的程序歸屬。")
    return False


def prepare_profiles(ports):
    """Prepare closed profiles before any worker starts Chrome; never kill one."""
    source_port = config.REMOTE_DEBUGGING_PORTS[0]
    source = config.get_chrome_user_data_dir(source_port)
    source_open = is_port_in_use(source_port)
    for port in ports:
        destination = config.get_chrome_user_data_dir(port)
        if is_port_in_use(port):
            if not owns_debug_port(port, destination):
                raise RuntimeError(f"偵錯埠 {port} 與其他程序衝突。")
            continue
        incomplete = os.path.join(destination, '.profile_sync_incomplete')
        if port == source_port or (os.path.isdir(destination) and not os.path.exists(incomplete)):
            continue
        if source_open or not os.path.isdir(source):
            if os.path.exists(incomplete):
                raise RuntimeError("Profile 同步尚未完成，請先關閉主偵錯 Chrome 再重試。")
            continue  # New, independent profile; worker will pause for login.
        os.makedirs(destination, exist_ok=True)
        with open(incomplete, 'w') as marker:
            marker.write('incomplete')
        skipped = _copy_tree_ignore_locked(source, destination)
        if skipped:
            raise RuntimeError("Profile 同步不完整，未標記成功。請關閉相關 Chrome 後重試。")
        with open(os.path.join(destination, '.profile_synced'), 'w') as marker:
            marker.write('synced')
        os.remove(incomplete)
