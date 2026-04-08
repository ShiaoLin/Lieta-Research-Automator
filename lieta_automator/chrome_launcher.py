import os
import re
import socket
import subprocess
import winreg
import shutil
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


def kill_all_debug_chrome_instances():
    """
    Kills all Chrome processes listening on the configured debug ports.
    Called before multi-window sync so profile files are not locked.
    """
    try:
        result = subprocess.check_output(
            'netstat -aon', shell=True, text=True, encoding='utf-8', errors='ignore'
        )
        pids = set()
        for port in config.REMOTE_DEBUGGING_PORTS:
            m = re.search(
                r'TCP\s+127\.0\.0\.1:' + str(port) + r'\s+.*?LISTENING\s+(\d+)', result
            )
            if m:
                pids.add(m.group(1))
        for pid in pids:
            subprocess.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True)
            logger.info(f"已關閉佔用偵錯 port 的 Chrome 程序 (PID {pid})。")
    except Exception as e:
        logger.warning(f"嘗試關閉 Chrome 程序時發生錯誤: {e}")


def sync_all_secondary_profiles():
    """
    Syncs all secondary Chrome profiles from the primary profile.
    Must be called while NO Chrome debug instances are running so that
    the primary profile's Cookies file is not locked.
    """
    source_profile_dir = config.get_chrome_user_data_dir(config.REMOTE_DEBUGGING_PORTS[0])
    if not os.path.exists(source_profile_dir):
        logger.warning("主 Profile 資料夾不存在，跳過同步。")
        return

    for port in config.REMOTE_DEBUGGING_PORTS[1:]:
        dest_profile_dir = config.get_chrome_user_data_dir(port)
        # Remove stale sync marker so we always do a fresh sync.
        sync_marker = os.path.join(dest_profile_dir, ".profile_synced")
        if os.path.exists(sync_marker):
            os.remove(sync_marker)

        logger.info(f"正在同步 Profile → {os.path.basename(dest_profile_dir)}...")
        os.makedirs(dest_profile_dir, exist_ok=True)
        skipped = _copy_tree_ignore_locked(source_profile_dir, dest_profile_dir)

        with open(sync_marker, 'w') as f:
            f.write("synced")

        if skipped:
            logger.warning(
                f"Profile '{os.path.basename(dest_profile_dir)}' 同步時跳過 {len(skipped)} 個鎖定檔案: "
                + ", ".join(os.path.basename(p) for p in skipped)
            )
        else:
            logger.info(f"Profile '{os.path.basename(dest_profile_dir)}' 同步完成。")


def _sync_profile_if_new(port: int, dest_profile_dir: str):
    """
    Legacy per-port sync used by single-window mode.
    For multi-window mode, call sync_all_secondary_profiles() instead.
    """
    if port == config.REMOTE_DEBUGGING_PORTS[0]:
        return

    source_profile_dir = config.get_chrome_user_data_dir(config.REMOTE_DEBUGGING_PORTS[0])
    sync_marker_file = os.path.join(dest_profile_dir, ".profile_synced")

    if os.path.exists(source_profile_dir) and not os.path.exists(sync_marker_file):
        logger.info(f"檢測到新的 Profile: {os.path.basename(dest_profile_dir)}，正在同步...")
        os.makedirs(dest_profile_dir, exist_ok=True)
        skipped = _copy_tree_ignore_locked(source_profile_dir, dest_profile_dir)
        with open(sync_marker_file, 'w') as f:
            f.write("synced")
        if skipped:
            logger.warning(
                f"Profile '{os.path.basename(dest_profile_dir)}' 同步完成，跳過 {len(skipped)} 個鎖定檔案: "
                + ", ".join(os.path.basename(p) for p in skipped)
            )
        else:
            logger.info(f"Profile '{os.path.basename(dest_profile_dir)}' 同步成功。")


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
        logger.info(f"Port {port} 已被占用，假設對應的 Chrome 偵錯模式已在執行。")
        return True

    logger.info(f"Port {port} 未被使用，正在尋找 Chrome 安裝路徑...")
    chrome_path = find_chrome_executable()
    if not chrome_path:
        logger.error("找不到 Chrome 安裝路徑。請確認已安裝 Chrome。")
        return False

    logger.info(f"正在為 Port {port} 啟動新的 Chrome 偵錯實例...")
    command = [
        f'"{chrome_path}"',  # Enclose the executable path in quotes
        f"--remote-debugging-port={port}",
        f'--user-data-dir="{user_data_dir}"',
        f'"{config.LIETA_PLATFORM_URL}"'
    ]
    try:
        # Join the command list into a single string to be executed by the shell.
        # This is safer for paths with spaces.
        subprocess.Popen(" ".join(command), shell=True)
        logger.info(f"已成功為 Port {port} 啟動 Chrome。請稍候瀏覽器開啟...")
        return True
    except Exception as e:
        logger.error(f"無法為 Port {port} 自動啟動 Chrome: {e}", exc_info=True)
        return False
