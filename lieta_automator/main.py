import sys
import os
import time
import tkinter as tk
from tkinter import messagebox
import threading

# Setup logging first, so it's available everywhere.
from .logger import logger
from .gui import TickerApp
from . import settings, config, chrome_launcher
from .scraper import LietaScraper

def _run_single_model_automated_task(scraper, tickers, model, dest_path, port, user_data_dir):
    """
    A thread worker for running a single model's automation in headless mode.
    Adapted from the GUI version.
    """
    try:
        if not chrome_launcher.launch_chrome_in_debug_mode(port, user_data_dir):
            raise Exception(f"[Port {port}] 無法啟動 Chrome 偵錯實例。")
        
        logger.info(f"[Port {port}] 等待 Chrome 啟動...")
        time.sleep(5)

        if not scraper.setup_driver():
            raise Exception(f"[Port {port}] 無法連接到 WebDriver。")

        if not scraper.check_login_status():
            raise Exception(f"[Port {port}] 使用者未登入。請先手動執行一次程式並登入。")

        logger.info(f"[Port {port}] WebDriver 設定成功，開始執行任務。")
        scraper.run_automation(tickers, model, dest_path)

    except Exception as e:
        logger.error(f"處理模型 {model} 時發生錯誤: {e}", exc_info=True)
        # Mark all tickers for this model as failed
        scraper.failed_tickers.extend([f"{t} ({model})" for t in tickers])
    finally:
        if scraper.driver:
            scraper.close_driver()

def run_automated_task():
    """
    Runs the automation in headless mode based on saved settings.
    This is the entry point for the scheduled task.
    """
    logger.info("--- 自動化排程任務啟動 ---")
    
    # 1. Load settings
    user_settings = settings.load_settings()

    # 2. Check if scheduling is actually enabled
    if not user_settings.get("schedule_enabled"):
        logger.info("排程未啟用，任務自動結束。")
        return

    # 3. Load tickers
    tickers_path = user_settings.get("last_ticker_path")
    if not tickers_path or not os.path.exists(tickers_path):
        logger.error(f"找不到 Ticker 檔案或路徑無效: {tickers_path}。任務中止。")
        return
    
    try:
        with open(tickers_path, "r", encoding="utf-8") as f:
            tickers = [line.strip().upper() for line in f if line.strip()]
        if not tickers:
            logger.warning(f"Ticker 檔案 {tickers_path} 為空。任務結束。")
            return
        logger.info(f"成功從 {tickers_path} 載入 {len(tickers)} 個 Tickers。")
    except Exception as e:
        logger.error(f"讀取 Ticker 檔案 {tickers_path} 失敗: {e}", exc_info=True)
        return

    # 4. Get other settings
    selected_models = user_settings.get("last_selected_models", [])
    destination_path = user_settings.get("last_destination_path", "")
    use_multi_window = user_settings.get("enable_multi_window", False)

    if not all([selected_models, destination_path]):
        logger.error("模型或儲存路徑未設定。請執行一次 GUI 模式來完成設定。任務中止。")
        return

    all_failed_tickers = []
    scrapers = []

    # 5. Run automation logic (adapted from gui.py)
    try:
        if use_multi_window:
            logger.info("--- 自動化開始 (多視窗模式) ---")
            if len(selected_models) > len(config.REMOTE_DEBUGGING_PORTS):
                logger.error(f"選擇的模型數量 ({len(selected_models)}) 超過可用埠號數量 ({len(config.REMOTE_DEBUGGING_PORTS)})。")
                return

            profiles_to_launch = []
            for i, model in enumerate(selected_models):
                port = config.REMOTE_DEBUGGING_PORTS[i]
                user_data_dir = config.get_chrome_user_data_dir(port)
                profiles_to_launch.append({'port': port, 'user_data_dir': user_data_dir, 'model': model})
                chrome_launcher._sync_profile_if_new(port, user_data_dir)
            
            threads = []
            for profile in profiles_to_launch:
                temp_path_for_port = config.get_temp_download_path_for_port(profile['port'])
                scraper = LietaScraper(download_path=temp_path_for_port, port=profile['port'])
                scrapers.append(scraper)
                
                thread = threading.Thread(
                    target=_run_single_model_automated_task,
                    args=(scraper, tickers.copy(), profile['model'], destination_path, profile['port'], profile['user_data_dir']),
                    daemon=True
                )
                threads.append(thread)
                thread.start()
                time.sleep(1)

            for thread in threads:
                thread.join()

        else: # Single window mode
            logger.info("--- 自動化開始 (單視窗模式) ---")
            port = config.REMOTE_DEBUGGING_PORTS[0]
            user_data_dir = config.get_chrome_user_data_dir(port)
            temp_path_for_port = config.get_temp_download_path_for_port(port)
            
            scraper = LietaScraper(download_path=temp_path_for_port, port=port)
            scrapers.append(scraper)

            if not chrome_launcher.launch_chrome_in_debug_mode(port, user_data_dir):
                raise Exception("無法啟動 Chrome 偵錯實例。" )
            
            time.sleep(5)

            if not scraper.setup_driver():
                raise Exception("無法連接到 WebDriver。")

            if not scraper.check_login_status():
                raise Exception("使用者未登入。請先手動執行一次程式並登入。")

            for model in selected_models:
                failed_for_model = scraper.run_automation(tickers.copy(), model, destination_path)
                all_failed_tickers.extend(failed_for_model)
            
            if scraper.driver:
                scraper.close_driver()

        # 6. Log summary
        for scraper in scrapers:
            all_failed_tickers.extend(scraper.failed_tickers)
        
        total_tasks = len(selected_models) * len(tickers)
        success_count = total_tasks - len(all_failed_tickers)
        summary_msg = f"任務完成! 總計: {total_tasks}, 成功: {success_count}, 失敗: {len(all_failed_tickers)}"
        if all_failed_tickers:
            unique_failures = sorted(list(set(all_failed_tickers)))
            summary_msg += f"\n失敗的項目 ({len(unique_failures)} 個): " + ", ".join(unique_failures)
        logger.info(summary_msg)

    except Exception as e:
        logger.critical(f"自動化排程過程中發生未預期的嚴重錯誤: {e}", exc_info=True)
    finally:
        logger.info("--- 自動化排程任務結束 ---")


def main():
    """
    Main entry point for the application.
    Checks for an automation flag and runs either the GUI or the headless task.
    """
    # Check for the headless/automated run flag
    if "--run-automated" in sys.argv:
        run_automated_task()
    else:
        # Original GUI startup
        try:
            logger.info("正在啟動 GUI...")
            root = tk.Tk()
            app = TickerApp(root)
            root.mainloop()
            logger.info("GUI 已關閉。" )
        except Exception as e:
            logger.critical(f"應用程式發生無法處理的嚴重錯誤: {e}", exc_info=True)
            # Use a fallback message box in case the root window is the issue
            messagebox.showerror("嚴重錯誤", f"應用程式發生無法處理的錯誤，請查看 log.jsonl。\n\n{e}")

if __name__ == "__main__":
    main()
