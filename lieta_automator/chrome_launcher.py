import os
import socket
import subprocess
import winreg
import shutil
from . import config
from .logger import logger

def _sync_profile_if_new(port: int, dest_profile_dir: str):
    """
    If this is the first time a secondary profile is being used, sync the primary
    profile's data to it to ensure a consistent state (logins, extensions, etc.).
    """
    # This logic only applies to secondary profiles (not the main one)
    if port == config.REMOTE_DEBUGGING_PORTS[0]:
        return

    source_profile_dir = config.get_chrome_user_data_dir(config.REMOTE_DEBUGGING_PORTS[0])
    sync_marker_file = os.path.join(dest_profile_dir, ".profile_synced")

    # Check if the source profile exists and the destination has not been synced before
    if os.path.exists(source_profile_dir) and not os.path.exists(sync_marker_file):
        logger.info(f"檢測到新的 Profile 資料夾: {os.path.basename(dest_profile_dir)}。正在從主 Profile 同步設定...")
        
        # Ensure the destination directory exists before copying
        os.makedirs(dest_profile_dir, exist_ok=True)
        
        try:
            # Copy the entire directory tree, overwriting existing files.
            # dirs_exist_ok=True is crucial for copying into an existing folder.
            shutil.copytree(source_profile_dir, dest_profile_dir, dirs_exist_ok=True)
            
            # Create a marker file to indicate that the sync is complete
            with open(sync_marker_file, 'w') as f:
                f.write("synced")
            logger.info(f"Profile '{os.path.basename(dest_profile_dir)}' 同步成功。")

        except Exception as e:
            logger.error(f"從 '{source_profile_dir}' 同步至 '{dest_profile_dir}' 時發生錯誤: {e}", exc_info=True)


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
    """
    # Before launching, sync the profile from the main one if it's a new profile
    _sync_profile_if_new(port, user_data_dir)

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
