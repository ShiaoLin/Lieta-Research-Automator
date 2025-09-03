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

def create_or_update_task(schedule_time: str, schedule_type: str = 'DAILY'):
    """
    建立或更新 Windows 排程工作。
    此操作需要系統管理員權限。

    :param schedule_time: 排程時間，格式為 "HH:MM"
    :param schedule_type: 排程類型 ('DAILY' 或 'WEEKDAYS')
    :return: (bool, str) 表示成功與否以及對應的訊息
    """
    if not is_admin():
        msg = "需要系統管理員權限才能設定排程。"
        logger.warning(msg)
        return False, msg

    executable_path = _get_run_script_path()
    
    # /TR 參數需要將執行檔路徑和其自身的參數視為一個單一的字串。
    # 格式: /TR "'C:\path\to\program.exe' --argument"
    task_run_command = f'"{executable_path}" --run-automated'

    # 基礎命令
    command = [
        'schtasks', '/Create',
        '/TN', TASK_NAME,
        '/TR', task_run_command, # 傳遞組合好的完整命令
    ]

    # 根據排程類型添加對應的參數
    if schedule_type == 'WEEKDAYS':
        command.extend(['/SC', 'WEEKLY', '/D', 'MON,TUE,WED,THU,FRI'])
        schedule_text = f"每週一至週五 {schedule_time}"
    else: # 預設為 DAILY
        command.extend(['/SC', 'DAILY'])
        schedule_text = f"每天 {schedule_time}"

    # 加上剩餘的通用參數
    command.extend(['/ST', schedule_time, '/F', '/RL', 'HIGHEST'])
    
    try:
        logger.info(f"正在建立或更新排程工作 '{TASK_NAME}'，時間: {schedule_time}，類型: {schedule_type}")
        logger.debug(f"執行 schtasks 命令: {' '.join(command)}")
        
        # 移除 check=True，手動處理回傳結果以便詳盡記錄
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='cp950',
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        # 無論成功或失敗，都記錄輸出
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        
        if stdout:
            logger.info(f"schtasks STDOUT: {stdout}")
        if stderr:
            logger.warning(f"schtasks STDERR: {stderr}")

        # 手動檢查回傳碼
        if result.returncode != 0:
            error_message = stderr if stderr else stdout
            raise subprocess.CalledProcessError(result.returncode, command, output=stdout, stderr=stderr)

        logger.info(f"成功設定排程工作 '{TASK_NAME}'")
        return True, f"成功設定排程於{schedule_text}"
        
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip() if e.stderr else str(e)
        final_msg = f"設定排程失敗: {error_message}"
        logger.error(final_msg)
        return False, final_msg
    except Exception as e:
        # 捕捉其他非預期的錯誤
        final_msg = f"設定排程時發生未預期錯誤: {e}"
        logger.error(final_msg, exc_info=True)
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

    command = ['schtasks', '/Delete', '/TN', TASK_NAME, '/F']
    
    try:
        logger.info(f"正在刪除排程工作 '{TASK_NAME}'")
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding='cp950',
            creationflags=subprocess.CREATE_NO_WINDOW
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
