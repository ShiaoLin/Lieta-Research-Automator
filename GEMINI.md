# Lieta Research 自動化工具架構說明
> **目的**
> 透過 GUI 讓非程式人員一次輸入多個 Ticker，批次呼叫 Lieta Research Web 平台下載各種量化模型（Gamma／Term／Smile／TV Code）結果，並依模型與時間戳自動整理檔案後搬移至指定資料夾。

## 1. 專案結構 (模組化)

應用程式已重構為一個高度模組化的 Python 套件，以提高可維護性、擴充性和穩定性。

```
/
├── lieta_automator/
│   ├── __init__.py           # 將資料夾標示為 Python 套件
│   ├── main.py               # 應用程式主邏輯進入點
│   ├── gui.py                # 包含 TickerApp 類別，處理使用者介面
│   ├── scraper.py            # 包含 LietaScraper 類別，處理網頁自動化
│   ├── chrome_launcher.py    # 處理啟動與檢查偵錯模式的 Chrome
│   ├── logger.py             # 設定日誌系統 (GUI + 檔案)
│   ├── settings.py           # 處理使用者設定的載入與儲存
│   └── config.py             # 存儲所有應用程式靜態設定值
│
├── .gitignore                # 告訴 Git 忽略哪些檔案
├── requirements.txt          # 列出所有必要的 Python 套件
├── run.py                    # **打包與執行的主要入口點**
├── user_settings.json        # 儲存使用者的偏好設定 (自動生成, 已被 gitignore)
├── log.jsonl                 # 結構化的日誌輸出檔案 (自動生成, 已被 gitignore)
└── 啟動偵錯模式Chrome.lnk    # 快速啟動 Chrome 的捷徑 (已被 gitignore)
```

## 2. 安裝與執行

### 2.1. 安裝依賴套件
在終端機中執行以下指令，安裝所有必要的函式庫：
```bash
pip install -r requirements.txt
```

| 類別 | 套件 | 用途 |
| --- | --- | --- |
| Web 自動化 | `selenium` | 控制 Chrome 瀏覽器、互動、下載檔案。 |
| GUI | `tkinter` | **Python 內建**，無需安裝。用於建立視覺化操作介面。 |
| Windows 捷徑 | `winshell` | 用於建立和管理 Windows 捷徑檔案。 |
| 系統 | `os`, `shutil`, `subprocess` | **Python 內建**。用於檔案與系統操作。 |

### 2.2. 執行程式
使用以下指令從專案根目錄啟動應用程式：
```bash
python run.py
```
程式啟動時會自動檢查 Chrome 是否以偵錯模式執行，並提供指引。

---

## 3. 模組與類別設計

| 模組 | 類別/函式 | 關鍵職責 |
| --- | --- | --- |
| `run.py` | (無類別) | - **應用程式主入口點**。<br>- 呼叫 `lieta_automator.main` 來啟動程式。 |
| `gui.py` | `TickerApp` | - 建立所有 GUI 元件，處理使用者互動。<br>- **啟動時，以安全模式清理並準備 `temp_downloads` 暫存資料夾**，避免因檔案鎖定導致的權限錯誤，提高程式穩定性。<br>- 在獨立執行緒中啟動 `LietaScraper`，防止介面凍結。<br>- 將日誌顯示在 GUI 上。 |
| `scraper.py` | `LietaScraper` | - 附掛到已在偵錯模式下執行的 Chrome。<br>- **檢查登入狀態**：透過檢查網址是否為 `/platform` 來確認。<br>- **執行 Selenium 操作**：包含切換模型、輸入 Ticker、點擊下載。<br>- **智慧等待**：提交 Ticker 後，會等待最多 60 秒，並每秒檢查一次結果是否載入。<br>- 處理檔案下載、重新命名和移動的邏輯。 |
| `chrome_launcher.py` | (函式) | - 自動尋找 Chrome 安裝路徑。<br>- 建立/更新用於啟動偵錯模式的 `.lnk` 捷徑。<br>- 檢查偵錯埠是否被占用，若否，則嘗試啟動 Chrome。 |
| `logger.py` | `logger` | - 設定全域日誌記錄器。<br>- **雙重輸出**：同時將日誌寫入 GUI 的文字方塊和根目錄的 `log.jsonl` 檔案。 |
| `settings.py` | (函式) | - 從 `user_settings.json` 載入使用者設定（如上次選擇的路徑）。<br>- 將新的使用者設定儲存回 JSON 檔案。 |
| `config.py` | (無類別) | - **動態路徑管理**：偵測執行環境 (PyInstaller 或 .py)，並提供正確的絕對基礎路徑 `BASE_DIR`。<br>- 集中管理所有**靜態**設定，如 URL、埠號、逾時秒數等。 |
| `main.py` | `main()` | - 包含應用程式的主要啟動邏輯，由 `run.py` 呼叫。 |

---

## 4. 檔案處理邏輯

### 4.1. 暫存資料夾 (`temp_downloads`) 運作邏輯

`temp_downloads` 資料夾是整個自動化流程穩定運作的關鍵，它扮演著一個**檔案暫存區**的角色。

#### 4.1.1. 必要性
- **統一的下載位置**: 程式需要一個明確、獨立的位置來接收 Chrome 下載的檔案，避免與系統的「下載」資料夾混淆。
- **可靠的檔案監控**: 透過監控一個專屬的資料夾，程式可以非常精確地偵測到新檔案的出現及其檔名。
- **避免命名衝突**: Lieta 網站下載的原始檔名可能都是一樣的（例如 `chart.html`）。暫存區允許程式先接收原始檔案，再根據模型和 Ticker 安全地**重新命名**，然後才移動到最終目的地，從而避免了檔案覆蓋問題。

#### 4.1.2. 生命週期
1.  **程式啟動時**: `gui.py` 中的 `_prepare_temp_dir` 函式會執行以下操作：
    - 確保 `temp_downloads` 資料夾存在（若不存在則建立）。
    - **清空資料夾內容**：為了避免上次異常關閉留下的舊檔案造成干擾，程式會逐一刪除資料夾內的所有檔案和子資料夾，但保留 `temp_downloads` 本身。這種方式比刪除整個資料夾更穩健，能有效防止因資料夾被鎖定而引發的 `PermissionError`。
2.  **自動化執行中**: `scraper.py` 在點擊下載後，會持續監控此資料夾，直到偵測到一個新的 `.html` 檔案出現。
3.  **檔案處理**: 一旦確認檔案已完整下載，`scraper.py` 會立刻將其**移動並重新命名**到使用者指定的最終儲存路徑中。
4.  **程式關閉時**: 雖然暫存檔在處理後會立刻被移走，但此資料夾會被保留，以便下次啟動時再次清空和使用。

### 4.2. 輸出檔案命名規則
- **圖表 HTML**：`{destination_path}/{Model}/{Ticker}/{YYYY-MM-DD_HH;MM}_{Ticker}_{Model}.html`
- **TV Code 文本**：`{destination_path}/TV Code/{YYYYMMDD}_TV Code.txt`

---
*（文件的其餘部分保持不變）*
