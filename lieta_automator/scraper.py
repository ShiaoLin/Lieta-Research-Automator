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
    Handles all Selenium web scraping and file manipulation logic for a single
    Chrome instance identified by a specific port.
    """

    def __init__(self, download_path, port):
        self.download_path = download_path
        self.port = port
        self.driver = None
        self.failed_tickers = []

    def setup_driver(self):
        """
        Sets up the Selenium WebDriver by connecting to an existing Chrome instance
        on the port specified during initialization.
        """
        try:
            logger.info(f"[Port {self.port}] 正在連接到 Chrome 瀏覽器...")
            chrome_options = Options()
            chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.port}")
            service = ChromeService()
            self.driver = webdriver.Chrome(service=service, options=chrome_options)

            # --- Set window position and size to avoid overlapping issues ---
            try:
                base_port = config.REMOTE_DEBUGGING_PORTS[0]
                window_index = self.port - base_port
                cascade_offset = 50
                pos_x = window_index * cascade_offset
                pos_y = window_index * cascade_offset
                
                self.driver.set_window_size(1200, 800)
                self.driver.set_window_position(pos_x, pos_y)
                logger.info(f"[Port {self.port}] 已將視窗移動至 ({pos_x}, {pos_y})。")
            except Exception as e:
                logger.warning(f"[Port {self.port}] 設定視窗位置或大小時發生非嚴重錯誤: {e}")
            # ----------------------------------------------------------------

            logger.info(f"[Port {self.port}] 成功連接到 Chrome。")
            return True
        except Exception as e:
            logger.error(f"[Port {self.port}] 無法連接到 Chrome 瀏覽器: {e}", exc_info=True)
            return False

    def check_login_status(self):
        """
        Checks if the user is logged in by verifying the URL.
        """
        try:
            logger.info(f"[Port {self.port}] 正在檢查登入狀態...")
            self.driver.get(config.LIETA_AUTOMATION_URL)
            time.sleep(3)
            current_url = self.driver.current_url
            logger.info(f"[Port {self.port}] 目前網址為: {current_url}")
            if self.driver.current_url == config.LIETA_AUTOMATION_URL:
                logger.info(f"[Port {self.port}] 網址符合預期，使用者已登入。")
                return True
            else:
                logger.warning(f"[Port {self.port}] 網址不符合預期 ({current_url})，使用者可能尚未登入。")
                return False
        except Exception as e:
            logger.error(f"[Port {self.port}] 檢查登入狀態時發生未知錯誤: {e}", exc_info=True)
            return False

    def run_automation(self, tickers, model, destination_path):
        """Main automation loop for a single model."""
        self.failed_tickers = []
        logger.info(f"--- [Port {self.port}] 開始處理模型: {model} ---")

        try:
            logger.info(f"[Port {self.port}] 導航至 Lieta 平台: {config.LIETA_AUTOMATION_URL}")
            self.driver.get(config.LIETA_AUTOMATION_URL)
            wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
            wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[role="combobox"]')))
            logger.info(f"[Port {self.port}] 模型選擇器按鈕已找到。")
        except Exception as e:
            logger.error(f"[Port {self.port}] 無法載入 Lieta 平台或找不到初始模型選擇器: {e}", exc_info=True)
            self.failed_tickers.extend([f"{ticker} ({model})" for ticker in tickers])
            return self.failed_tickers

        # --- Select the model ---
        selection_successful = False
        try:
            for attempt in range(2):
                logger.info(f"[Port {self.port}] 第 {attempt + 1} 次嘗試選擇模型: {model}")
                wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
                
                model_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[role="combobox"]')))
                self.driver.execute_script("arguments[0].click();", model_button)
                
                model_option = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{model}')]")))
                self.driver.execute_script("arguments[0].click();", model_option)
                
                try:
                    wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, 'button[role="combobox"]'), model))
                    logger.info(f"[Port {self.port}] 驗證成功: 目前模型已切換為 {model}")
                    selection_successful = True
                    break
                except Exception:
                    logger.warning(f"[Port {self.port}] 第 {attempt + 1} 次嘗試驗證失敗。")
                    if attempt == 0: time.sleep(3)

            if not selection_successful:
                raise Exception("重試後仍無法成功選擇模型。")

        except Exception as e:
            logger.error(f"[Port {self.port}] 無法選擇模型 {model}，將跳過此模型的所有 Ticker。原因: {e}", exc_info=True)
            self.failed_tickers.extend([f"{ticker} ({model})" for ticker in tickers])
            return self.failed_tickers

        # --- Process tickers for the selected model ---
        if model == "TV Code":
            self._process_tv_code(tickers, destination_path)
        else:
            self._process_html_model(model, tickers, destination_path)

        logger.info(f"--- [Port {self.port}] 模型 {model} 處理完畢 ---")
        return self.failed_tickers

    def _process_html_model(self, model, tickers, destination_path):
        """Processes models that download an HTML file."""
        wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
        long_wait = WebDriverWait(self.driver, 90, poll_frequency=0.3)

        # Set download path once before the loop to avoid repeated CDP calls per ticker.
        os.makedirs(self.download_path, exist_ok=True)
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": self.download_path})

        total_tickers = len(tickers)
        next_ticker_pre_typed = False

        def ts(t0):
            """Returns elapsed seconds since t0 as a formatted string."""
            return f"+{time.time() - t0:.2f}s"

        for i, ticker in enumerate(tickers):
            t_ticker_start = time.time()
            logger.info(f"[TIMING] ({i+1}/{total_tickers}) [{model}|{ticker}] 開始")
            try:
                chart_loaded = False
                for attempt in range(2):
                    if attempt == 0 and next_ticker_pre_typed:
                        logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 已預先輸入，略過打字")
                        next_ticker_pre_typed = False
                    else:
                        t0 = time.time()
                        ticker_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="Ticker"]')))
                        ticker_input.clear()
                        ticker_input.send_keys(ticker)
                        if i == 0:
                            time.sleep(1)
                        logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 輸入完成 (耗時 {time.time()-t0:.2f}s)")

                    t0 = time.time()
                    submit_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]')))
                    submit_button.click()
                    logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} submit 已點擊 (等待submit按鈕 {time.time()-t0:.2f}s)")

                    t0 = time.time()
                    loaded = self._submit_with_server_error_retry(
                        wait, long_wait, t_ticker_start, self._wait_for_chart_or_error
                    )
                    if loaded:
                        logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} SVG 出現 (等待圖表 {time.time()-t0:.2f}s)")
                        chart_loaded = True
                        break
                    else:
                        logger.warning(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} SVG 等待失敗 ({time.time()-t0:.2f}s)")
                        next_ticker_pre_typed = False
                        if attempt == 0:
                            logger.info(f"[TIMING] [{model}|{ticker}] 重試...")
                if not chart_loaded:
                    raise Exception("重試後仍然無法載入圖表。")

                t0 = time.time()
                files_before_download = set(os.listdir(self.download_path))
                download_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '下載')]")))
                download_button.click()
                logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 下載按鈕已點擊 (等待按鈕 {time.time()-t0:.2f}s)")

                t0 = time.time()
                downloaded_file_path = self._wait_for_new_file(files_before_download, ".html")
                if not downloaded_file_path:
                    raise Exception("下載超時或未找到新的 .html 檔案。")
                logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 檔案出現 (等待檔案 {time.time()-t0:.2f}s)")

                t0 = time.time()
                self._wait_for_download_complete(downloaded_file_path)
                logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 下載完成 (確認大小穩定 {time.time()-t0:.2f}s)")

                next_index = i + 1
                if next_index < total_tickers:
                    t0 = time.time()
                    try:
                        next_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="Ticker"]')))
                        next_input.clear()
                        next_input.send_keys(tickers[next_index])
                        next_ticker_pre_typed = True
                        logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 預先輸入 {tickers[next_index]} 完成 (耗時 {time.time()-t0:.2f}s)")
                    except Exception:
                        next_ticker_pre_typed = False

                t0 = time.time()
                target_dir = os.path.join(destination_path, model, ticker.upper())
                os.makedirs(target_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y-%m-%d_%H;%M")
                new_filename = f"{timestamp}_{ticker.upper()}_{model}.html"
                new_filepath = os.path.join(target_dir, new_filename)
                # Use copy2 instead of move: copy only requires read access and completes
                # immediately even while Windows security scanning holds a write lock on
                # the freshly downloaded file. The temp file is cleaned up on next launch.
                shutil.copy2(downloaded_file_path, new_filepath)
                logger.info(f"[TIMING] [{model}|{ticker}] {ts(t_ticker_start)} 檔案複製完成 (copy {time.time()-t0:.2f}s) | 本 ticker 總耗時 {time.time()-t_ticker_start:.2f}s")

            except Exception as e:
                next_ticker_pre_typed = False
                logger.error(f"失敗: [Port:{self.port}|{model}] - {ticker}. 原因: {str(e).splitlines()[0]}", exc_info=True)
                self.failed_tickers.append(f"{ticker} ({model})")

    def _process_tv_code(self, tickers, destination_path):
        """Processes the 'TV Code' model which scrapes text."""
        wait = WebDriverWait(self.driver, config.SELENIUM_TIMEOUT)
        long_wait = WebDriverWait(self.driver, 90, poll_frequency=0.3)
        target_dir = os.path.join(destination_path, "TV Code")
        os.makedirs(target_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")
        output_filepath = os.path.join(target_dir, f"{timestamp}_TV Code.txt")
        for i, ticker in enumerate(tickers):
            logger.info(f"({i+1}/{len(tickers)}) [Port:{self.port}|TV Code] 處理中: {ticker}")
            try:
                text_loaded = False
                for attempt in range(2):
                    logger.info(f"[Port {self.port}] 第 {attempt + 1} 次嘗試提交 {ticker}...")
                    ticker_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="Ticker"]')))
                    ticker_input.clear()
                    ticker_input.send_keys(ticker)
                    if i == 0:
                        logger.info("為第一個 Ticker 增加 1 秒延遲...")
                        time.sleep(1)
                    submit_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]')))
                    submit_button.click()
                    ticker_upper = ticker.upper()
                    logger.info(f"[Port {self.port}] 正在等待 {ticker} 的 TV Code (最多 90 秒，含自動重試)...")
                    loaded = self._submit_with_server_error_retry(
                        wait, long_wait, time.time(), self._wait_for_tv_code_or_error, ticker_upper
                    )
                    if loaded:
                        logger.info(f"[Port {self.port}] 成功取得 {ticker} 的 TV Code。")
                        text_loaded = True
                        break
                    else:
                        logger.warning(f"[Port {self.port}] 第 {attempt+1} 次嘗試失敗。")
                        if attempt == 0: logger.info("正在準備重試...")
                if not text_loaded:
                    raise Exception("重試後仍然無法取得 TV Code。")
                ticker_upper = ticker.upper()
                p_element = self.driver.find_element(By.XPATH, f"//p[contains(text(), '{ticker_upper}:')] ")
                code_text = p_element.text
                with open(output_filepath, "a", encoding="utf-8") as f:
                    f.write(code_text + "\n")
                logger.info(f"成功: [Port:{self.port}|TV Code] for {ticker.upper()} 已儲存。")
            except Exception as e:
                logger.error(f"失敗: [Port:{self.port}|TV Code] - {ticker}. 原因: {str(e).splitlines()[0]}", exc_info=True)
                self.failed_tickers.append(f"{ticker} (TV Code)")

    # --- Server-error detection helpers ---

    _SERVER_ERROR_RETRIES = 100  # keep retrying until SVG appears or limit reached

    # JavaScript: case-insensitive search for "try again later" in any leaf DOM element
    _JS_SERVER_ERROR = (
        "return Array.from(document.querySelectorAll('*')).some("
        "  el => el.childElementCount === 0 &&"
        "  el.textContent.toLowerCase().includes('try again later')"
        ");"
    )

    def _wait_for_chart_or_error(self, long_wait: WebDriverWait):
        """
        Waits until svg.main-svg appears ("loaded") or a server-error toast is
        detected ("error"). Returns None on 90-second timeout.
        """
        js = self._JS_SERVER_ERROR

        class _Condition:
            def __call__(self, driver):
                if driver.find_elements(By.CSS_SELECTOR, 'svg.main-svg'):
                    return "loaded"
                if driver.execute_script(js):
                    return "error"
                return False

        try:
            return long_wait.until(_Condition())
        except TimeoutException:
            return None

    def _wait_for_tv_code_or_error(self, long_wait: WebDriverWait, ticker_upper: str):
        """Same dual-detection for TV Code model."""
        js = self._JS_SERVER_ERROR

        class _Condition:
            def __call__(self, driver):
                if driver.execute_script(js):
                    return "error"
                paragraphs = driver.find_elements(By.XPATH, "//p")
                if any(f"{ticker_upper}:" in p.text for p in paragraphs):
                    return "loaded"
                return False

        try:
            return long_wait.until(_Condition())
        except TimeoutException:
            return None

    def _submit_with_server_error_retry(self, wait, long_wait, t_ticker_start, wait_fn, *args):
        """
        After each submit, calls wait_fn to detect success or server error.
        On "error", waits 1 second and re-clicks submit.
        Keeps retrying up to _SERVER_ERROR_RETRIES times or until SVG appears.
        Returns True if "loaded", False if timeout or retry limit reached.
        """
        def ts():
            return f"+{time.time() - t_ticker_start:.2f}s"

        for retry in range(self._SERVER_ERROR_RETRIES):
            result = wait_fn(long_wait, *args)
            if result == "loaded":
                return True
            if result == "error":
                logger.warning(
                    f"[Port {self.port}] {ts()} 偵測到伺服器錯誤，"
                    f"第 {retry + 1}/{self._SERVER_ERROR_RETRIES} 次自動重新提交..."
                )
                time.sleep(1)
                try:
                    submit_button = wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[type="submit"]'))
                    )
                    submit_button.click()
                    logger.info(f"[Port {self.port}] {ts()} 已重新提交。")
                except Exception as e:
                    logger.error(f"[Port {self.port}] 重新提交失敗: {e}")
                    return False
            else:
                # 90-second timeout with no result and no error message
                return False

        logger.error(f"[Port {self.port}] 已達重試上限 ({self._SERVER_ERROR_RETRIES} 次)，仍未取得結果。")
        return False

    # ----------------------------------------

    def _wait_for_new_file(self, files_before, extension, timeout=90):
        """Waits for a new file with a specific extension to appear."""
        timeout_end = time.time() + timeout
        while time.time() < timeout_end:
            files_after = set(os.listdir(self.download_path))
            new_files = files_after - files_before
            if new_files:
                for file in new_files:
                    if file.endswith(extension):
                        return os.path.join(self.download_path, file)
            time.sleep(0.3)
        return None

    def _wait_for_download_complete(self, filepath, timeout=90):
        """Waits for a file to be fully downloaded by checking if the file size is stable."""
        last_size = -1
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(filepath):
                try:
                    current_size = os.path.getsize(filepath)
                    if current_size == last_size and current_size > 0:
                        return True
                    last_size = current_size
                except OSError:
                    pass
            time.sleep(0.3)
        raise Exception(f"Download timed out for {os.path.basename(filepath)}")

    def close_driver(self):
        """Closes the WebDriver."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info(f"[Port {self.port}] WebDriver 已成功關閉。")
            except Exception as e:
                logger.error(f"[Port {self.port}] 關閉 WebDriver 時發生錯誤: {e}", exc_info=True)
            finally:
                self.driver = None