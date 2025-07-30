import os
import sys
import tkinter as tk
from tkinter import messagebox

# Setup logging first, so it's available everywhere.
from .logger import logger
from . import chrome_launcher
from .gui import TickerApp


def main():
    """
    Main entry point for the application.
    - Ensures Chrome is running in debug mode.
    - Initializes and runs the Tkinter GUI.
    """

    if sys.platform == "win32":
        logger.info("--- 正在準備 Chrome 瀏覽器 ---")
        # The new `ensure_chrome_is_running` doesn't need the project_root argument
        # as it gets the base directory directly from the config module.
        is_chrome_ready = chrome_launcher.ensure_chrome_is_running()
        logger.info("--- Chrome 準備完畢 ---")
        if not is_chrome_ready:
            # The launcher itself will log the specific error.
            messagebox.showerror(
                "Chrome 啟動失敗",
                "無法自動尋找或啟動 Chrome.\n\n"
                "請檢查 log.jsonl 檔案以獲取詳細資訊，或手動執行 '啟動偵錯模式Chrome.lnk' 後再試一次。"
            )
            return
    else:
        logger.warning("偵測到非 Windows 平台，請手動以偵錯模式啟動 Chrome。")
        logger.warning(f"指令範例: google-chrome --remote-debugging-port={config.REMOTE_DEBUGGING_PORT}")

    try:
        logger.info("正在啟動 GUI...")
        root = tk.Tk()
        app = TickerApp(root)
        # The protocol is now set inside the TickerApp's __init__ method.
        root.mainloop()
        logger.info("GUI 已關閉。")
    except Exception as e:
        logger.critical(f"應用程式發生無法處理的嚴重錯誤: {e}", exc_info=True)
        messagebox.showerror("嚴重錯誤", f"應用程式發生無法處理的錯誤，請查看 log.jsonl。\n\n{e}")

if __name__ == "__main__":
    main()
