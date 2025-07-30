import os
import socket
import sys
import winreg
import winshell

from . import config
from .logger import logger

# --- Constants ---
SHORTCUT_NAME = "啟動偵錯模式Chrome.lnk"

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


def create_debug_shortcut(project_root, chrome_path):
    """
    Creates a shortcut in the project root to launch Chrome in debug mode.
    """
    shortcut_path = os.path.join(project_root, SHORTCUT_NAME)
    target = chrome_path
    
    user_data_full_path = os.path.join(project_root, config.CHROME_USER_DATA_DIR)
    
    args = (
        f'--remote-debugging-port={config.REMOTE_DEBUGGING_PORT} '
        f'--user-data-dir="{user_data_full_path}" '
        f'"{config.LIETA_PLATFORM_URL}"'
    )

    with winshell.shortcut(shortcut_path) as link:
        link.path = target
        link.arguments = args
        link.description = f"以遠端偵錯模式 (Port {config.REMOTE_DEBUGGING_PORT}) 啟動 Chrome"
        link.working_directory = project_root

    return shortcut_path


def is_port_in_use(port):
    """
    Checks if a given TCP port is already in use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except socket.error:
            return True


def ensure_chrome_is_running(project_root):
    """
    Main function to ensure Chrome is running in debug mode.
    """
    logger.info("正在尋找 Chrome 安裝路徑...")
    chrome_path = find_chrome_executable()
    if not chrome_path:
        logger.error("找不到 Chrome 安裝路徑。請確認已安裝 Chrome。")
        return False
    logger.info(f"成功找到 Chrome: {chrome_path}")

    logger.info(f"正在建立/更新 '{SHORTCUT_NAME}' 捷徑...")
    try:
        shortcut_path = create_debug_shortcut(project_root, chrome_path)
        logger.info(f"成功建立捷徑於: {shortcut_path}")
    except Exception as e:
        logger.error(f"無法建立捷徑: {e}", exc_info=True)
        return False

    logger.info(f"正在檢查 Port {config.REMOTE_DEBUGGING_PORT} 是否已被使用...")
    if is_port_in_use(config.REMOTE_DEBUGGING_PORT):
        logger.info(f"Port {config.REMOTE_DEBUGGING_PORT} 已被占用，假設 Chrome 偵錯模式已在執行。")
        return True

    logger.info(f"Port {config.REMOTE_DEBUGGING_PORT} 未被使用，正在嘗試啟動 Chrome...")
    try:
        os.startfile(shortcut_path)
        logger.info("已透過捷徑啟動 Chrome。請稍候瀏覽器開啟...")
        return True
    except Exception as e:
        logger.error(f"無法自動啟動 Chrome: {e}", exc_info=True)
        return False
