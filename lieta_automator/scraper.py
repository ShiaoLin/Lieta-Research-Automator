import os
import shutil
import time
import traceback
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import config
from .logger import logger


class LietaScraper:
    """
    Handles all Selenium web scraping and file manipulation logic.
    """

    def __init__(self, download_path):
        self.download_path = download_path
        self.driver = None
        self.failed_tickers = []

    def setup_driver(self):
        """
        Sets up the Selenium WebDriver by connecting to an existing Chrome instance.
        """
        try:
            chrome_options = Options()
            chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{config.REMOTE_DEBUGGING_PORT}")
            service = ChromeService()
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            return True
        except Exception as e:
            logger.error(f"無法連接到 Chrome 瀏覽器: {e}", exc_info=True)
            return False

    def check_login_status(self):
        """
        Checks if the user is logged in by verifying the URL.
        """
        try:
            logger.info("正在檢查登入狀態...")
            # Navigate to the page that requires login
            self.driver.get(config.LIETA_AUTOMATION_URL)
            # Wait for potential redirection if not logged in
            time.sleep(3) 
            
            current_url = self.driver.current_url
            logger.info(f"目前網址為: {current_url}")

            # If we are still on the platform URL, we are logged in.
            if self.driver.current_url == config.LIETA_AUTOMATION_URL:
                logger.info("網址符合預期，使用者已登入。")
                return True
            else:
                logger.warning(f"網址不符合預期 ({current_url})，使用者可能尚未登入。")
                return False
        except Exception as e:
            logger.error(f"檢查登入狀態時發生未知錯誤: {e}", exc_info=True)
            return False

    def run_automation(self, tickers, models, destination_path):
        """Main automation loop."""
        self.failed_tickers = []
        total_tasks = len(tickers) * len(models)
        completed_tasks = 0

        try:
            logger.info(f"導航至 Lieta 平台: {config.LIETA_AUTOMATION_URL}")
            self.driver.get(config.LIETA_AUTOMATION_URL)
            logger.info(f"導航成功。目前網址: {self.driver.current_url}")

            wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
            wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[role="combobox"]')))
            logger.info("模型選擇器按鈕已找到且可點擊。")

        except Exception as e:
            logger.error(f"無法載入 Lieta 平台或找不到初始模型選擇器: {e}", exc_info=True)
            return self.failed_tickers, total_tasks

        for model in models:
            logger.info(f"--- 切換到模型: {model} ---")
            selection_successful = False
            try:
                for attempt in range(2):
                    logger.info(f"第 {attempt + 1} 次嘗試選擇模型: {model}")
                    wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
                    
                    model_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[role="combobox"]')))
                    self.driver.execute_script("arguments[0].click();", model_button)
                    
                    model_option = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{model}')]")))
                    self.driver.execute_script("arguments[0].click();", model_option)
                    
                    try:
                        wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, 'button[role="combobox"]'), model))
                        logger.info(f"驗證成功: 目前模型已切換為 {model}")
                        selection_successful = True
                        break
                    except Exception:
                        logger.warning(f"第 {attempt + 1} 次嘗試驗證失敗。")
                        if attempt == 0: time.sleep(3)

                if not selection_successful:
                    raise Exception("重試後仍無法成功選擇模型。")

            except Exception as e:
                logger.error(f"無法選擇模型 {model}，將跳過此模型。原因: {e}", exc_info=True)
                completed_tasks += len(tickers)
                self.failed_tickers.extend([f"{ticker} ({model})" for ticker in tickers])
                continue

            if model == "TV Code":
                completed_tasks = self._process_tv_code(tickers, destination_path, completed_tasks, total_tasks)
            else:
                completed_tasks = self._process_html_model(model, tickers, destination_path, completed_tasks, total_tasks)

        logger.info("--- 所有任務處理完畢 ---")
        return self.failed_tickers, total_tasks

    def _process_html_model(self, model, tickers, destination_path, completed_tasks, total_tasks):
        """Processes models that download an HTML file, with retry logic."""
        wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
        for ticker in tickers:
            completed_tasks += 1
            logger.info(f"({completed_tasks}/{total_tasks}) 處理中: {model} - {ticker}")
            try:
                chart_loaded = False
                for attempt in range(2):
                    logger.info(f"第 {attempt + 1} 次嘗試提交 {ticker}...")
                    
                    ticker_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="Ticker"]')))
                    ticker_input.clear()
                    ticker_input.send_keys(ticker)
                    
                    submit_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]')))
                    submit_button.click()

                    try:
                        logger.info(f"正在等待 {ticker} 的圖表資料 (最多 60 秒)...")
                        long_wait = WebDriverWait(self.driver, 60, poll_frequency=1)
                        long_wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'svg.main-svg')))
                        logger.info("圖表已載入，準備下載。")
                        chart_loaded = True
                        break
                    except Exception:
                        logger.warning(f"第 {attempt+1} 次提交在 60 秒後超時。")
                        if attempt == 0:
                            logger.info("正在準備重試...")

                if not chart_loaded:
                    raise Exception("重試後仍然無法載入圖表。")

                self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": self.download_path})

                files_before_download = set(os.listdir(self.download_path))
                download_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '下載')]")))
                download_button.click()

                downloaded_file_path = self._wait_for_new_file(files_before_download, ".html")
                if not downloaded_file_path:
                    raise Exception("下載超時或未找到新的 .html 檔案。")

                self._wait_for_download_complete(downloaded_file_path)

                target_dir = os.path.join(destination_path, model, ticker.upper())
                os.makedirs(target_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y-%m-%d_%H;%M")
                new_filename = f"{timestamp}_{ticker.upper()}_{model}.html"
                new_filepath = os.path.join(target_dir, new_filename)

                shutil.move(downloaded_file_path, new_filepath)
                logger.info(f"成功: {new_filename} 已儲存。")

            except Exception as e:
                logger.error(f"失敗: {model} - {ticker}. 原因: {str(e).splitlines()[0]}", exc_info=True)
                self.failed_tickers.append(f"{ticker} ({model})")
        return completed_tasks

    def _process_tv_code(self, tickers, destination_path, completed_tasks, total_tasks):
        """Processes the 'TV Code' model which scrapes text, with retry logic."""
        wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
        target_dir = os.path.join(destination_path, "TV Code")
        os.makedirs(target_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")
        output_filepath = os.path.join(target_dir, f"{timestamp}_TV Code.txt")

        for ticker in tickers:
            completed_tasks += 1
            logger.info(f"({completed_tasks}/{total_tasks}) 處理中: TV Code - {ticker}")
            try:
                text_loaded = False
                for attempt in range(2):
                    logger.info(f"第 {attempt + 1} 次嘗試提交 {ticker}...")
                    
                    ticker_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="Ticker"]')))
                    ticker_input.clear()
                    ticker_input.send_keys(ticker)
                    
                    submit_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]')))
                    submit_button.click()

                    try:
                        logger.info(f"正在等待 {ticker} 的 TV Code (最多 60 秒)...")
                        long_wait = WebDriverWait(self.driver, 60, poll_frequency=1)
                        ticker_upper = ticker.upper()
                        long_wait.until(EC.text_to_be_present_in_element((By.XPATH, "//p"), f"{ticker_upper}:"))
                        logger.info(f"成功取得 {ticker} 的 TV Code。")
                        text_loaded = True
                        break
                    except Exception:
                        logger.warning(f"第 {attempt+1} 次提交在 60 秒後超時。")
                        if attempt == 0:
                            logger.info("正在準備重試...")
                
                if not text_loaded:
                    raise Exception("重試後仍然無法取得 TV Code。")

                ticker_upper = ticker.upper()
                p_element = self.driver.find_element(By.XPATH, f"//p[contains(text(), '{ticker_upper}:')] ")
                code_text = p_element.text

                with open(output_filepath, "a", encoding="utf-8") as f:
                    f.write(code_text + "\n")
                
                logger.info(f"成功: TV Code for {ticker.upper()} 已儲存。")

            except Exception as e:
                logger.error(f"失敗: TV Code - {ticker}. 原因: {str(e).splitlines()[0]}", exc_info=True)
                self.failed_tickers.append(f"{ticker} (TV Code)")
        return completed_tasks

    def _wait_for_new_file(self, files_before, extension, timeout=60):
        """Waits for a new file with a specific extension to appear."""
        timeout_end = time.time() + timeout
        while time.time() < timeout_end:
            files_after = set(os.listdir(self.download_path))
            new_files = files_after - files_before
            if new_files:
                for file in new_files:
                    if file.endswith(extension):
                        return os.path.join(self.download_path, file)
            time.sleep(1)
        return None

    def _wait_for_download_complete(self, filepath, timeout=60):
        """Waits for a file to be fully downloaded by checking if the file size is stable."""
        seconds = 0
        last_size = -1
        while seconds < timeout:
            if os.path.exists(filepath):
                try:
                    current_size = os.path.getsize(filepath)
                    if current_size == last_size and current_size > 0:
                        time.sleep(1) # Wait one more second to be safe
                        return True
                    last_size = current_size
                except OSError:
                    pass # File might be locked
            time.sleep(1)
            seconds += 1
        raise Exception(f"Download timed out for {os.path.basename(filepath)}")

    def close_driver(self):
        """Closes the WebDriver."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("WebDriver 已成功關閉。")
            except Exception as e:
                logger.error(f"關閉 WebDriver 時發生錯誤: {e}", exc_info=True)
            finally:
                self.driver = None



