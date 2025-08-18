# lieta_automator/scheduler.py
import subprocess
import sys
import os
import ctypes
from .config import TASK_NAME
from .logger import logger

def is_admin():
    """檢查目前使用者是否具有系統管理員權限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except AttributeError:
        # 非 Windows 系統，或發生其他錯誤
        return False

def _get_python_executable():
    """取得 Python 解譯器的絕對路徑"""
    # 在 PyInstaller 打包的環境中，sys.executable 是主程式的路徑
    # 在開發環境中，它是 python.exe 的路徑
    return sys.executable

def _get_run_script_path():
    """
    取得主執行腳本 run.py 的絕對路徑。
    在 PyInstaller 環境中，腳本會被打包進執行檔，所以我們直接用 sys.executable。
    """
    # 檢查是否為 PyInstaller 打包的應用
    if getattr(sys, 'frozen', False):
        # 'frozen' 屬性由 PyInstaller 設定
        return sys.executable
    else:
        # 在開發環境中，找到 run.py
        # __file__ -> .../lieta_automator/scheduler.py
        # os.path.dirname(__file__) -> .../lieta_automator
        # os.path.dirname(...) -> .../
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(project_root, "run.py")

def is_task_scheduled():
    """檢查排程工作是否已經存在"""
    try:
        # 使用 schtasks 查詢，並將輸出導向 DEVNULL 避免顯示在控制台
        subprocess.check_call(
            f'schtasks /Query /TN "{TASK_NAME}"',
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except subprocess.CalledProcessError:
        # 如果任務不存在，schtasks 會返回非零的退出碼
        return False

def create_or_update_task(schedule_time: str):
    """
    建立或更新 Windows 排程工作。
    此操作需要系統管理員權限。

    :param schedule_time: 排程時間，格式為 "HH:MM"
    :return: (bool, str) 表示成功與否以及對應的訊息
    """
    if not is_admin():
        msg = "需要系統管理員權限才能設定排程。"
        logger.warning(msg)
        return False, msg

    executable_path = _get_run_script_path()
    
    # 正確的 /TR 格式應該是 ""C:\path\to\program.exe" --argument1"
    # 將可執行檔路徑和其參數一起作為一個字串傳遞給 /TR
    task_run_command = f'"{executable_path}" --run-automated'

    # 組建命令。使用 shell=True 時，將整個命令組合成一個字串是可靠的方式。
    # 確保 TASK_NAME 也被引號包圍。
    command = (
        f'schtasks /Create /TN "{TASK_NAME}" '
        f'/TR "{task_run_command}" '
        f'/SC DAILY /ST {schedule_time} /F /RL HIGHEST'
    )
    
    try:
        logger.info(f"正在建立或更新排程工作 '{TASK_NAME}'，時間: {schedule_time}")
        logger.debug(f"執行 schtasks 命令: {command}")
        
        subprocess.run(
            command,
            check=True,
            shell=True,
            capture_output=True,
            text=True,
            encoding='cp950'
        )
        logger.info(f"成功設定排程工作 '{TASK_NAME}'")
        return True, f"成功設定排程於每天 {schedule_time}"
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip()
        if not error_message:
            error_message = e.stdout.strip()
        
        final_msg = f"設定排程失敗: {error_message}"
        logger.error(final_msg)
        return False, final_msg


def delete_task():
    """
    刪除 Windows 排程工作。
    此操作需要系統管理員權限。
    
    :return: (bool, str) 表示成功與否以及對應的訊息
    """
    if not is_admin():
        msg = "需要系統管理員權限才能刪除排程。"
        logger.warning(msg)
        return False, msg

    if not is_task_scheduled():
        logger.info("排程工作不存在，無需刪除。" )
        return True, "排程本來就不存在。"

    command = f'schtasks /Delete /TN "{TASK_NAME}" /F'
    
    try:
        logger.info(f"正在刪除排程工作 '{TASK_NAME}'")
        subprocess.run(
            command,
            check=True,
            shell=True,
            capture_output=True,
            text=True,
            encoding='cp950'
        )
        logger.info(f"成功刪除排程工作 '{TASK_NAME}'")
        return True, "已成功取消自動排程。"
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip()
        if not error_message:
            error_message = e.stdout.strip()
            
        final_msg = f"刪除排程失敗: {error_message}"
        logger.error(final_msg)
        return False, final_msg
