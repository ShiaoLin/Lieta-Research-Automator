# Lieta Research Automator 1.2.0

1.2.0 新增 Table 模型，支援下載、狀態顯示、登入後繼續、失敗重試及批次續跑。沿用 HTML 存放規則：`目的地/Table/TICKER/YYYY-MM-DD_HH;MM_TICKER_Table.html`。詳細見 [1.2.0 說明](docs/release-1.2.0.md)。

Gamma、Term、Smile、TV Code、Table 可各用一個視窗，獨立跑完整份清單。所有視窗共用請求排程，預設最多一個請求等待結果，存檔不占用等待名額。

## 使用方式

將 `LietaAutomator_1.2.0.exe` 放在原程式資料夾，可沿用設定與 Chrome Profile。升級保留既有模型勾選，請勾選新增的 Table 再開始；續跑舊批次只處理該批次原本的模型。開啟偏好設定勾選多視窗後開始；既有使用者的單視窗設定會保留。全選五模型時會使用第五個 Profile（9226），第一次使用可能需要登入，狀態面板會顯示「等待登入」。登入後按該模型的「登入後繼續」，不影響其他正常視窗。

淺色下載工作台提供左側設定、右側五模型卡片、批次總覽與冷卻倒數，下方為最後總結及執行紀錄。缺少清單、模型或目的地時顯示提示；下載期間鎖定設定。「停止並保存進度」會通知工作執行緒結束，保留視窗供查看及續跑。小視窗可垂直捲動，鍵盤焦點會帶到可見位置。續跑會顯示原批次的模型與實際儲存位置。

「開始下載」下方的「重試失敗項目」使用原批次紀錄，只請求未完成或檔案驗證失敗的項目；重新開啟程式後可選擇舊批次。最後總結列出各模型完成數與所有未完成 ticker，支援捲動及複製。

主視窗提供「續跑批次」，可選擇 `runs/batch_*.json` 或舊版單模型紀錄。已完成檔案會重新核對，缺失或變更的項目會補抓。關閉程式時停止新提交、等待工作執行緒保存進度；不強制關閉其他 Chrome、不清除尚未完成的下載資料。

## 請求與恢復規則

- 所有視窗的提交至少間隔 5 秒；程式不會自動把等待上限由 1 增加到 2。
- Try Again 或 90 秒未取得有效結果，觸發所有視窗共用冷卻 10、20、40、80、120 秒，上限 120 秒。成功會逐步降低後續冷卻級別，不提前縮短正在生效的冷卻。
- Try Again 冷卻後重試同一項一次，每輪一般提交最多兩次。90 秒逾時直接延後補抓，包含按鈕一直顯示載入的狀況。
- 頁面重整完成且共用冷卻結束後，才放行替代請求。重整不代表伺服器已停止原運算，日誌會記錄放棄追蹤的請求。
- Unauthorized 只恢復該視窗：最多兩次重整及兩次額外重送。仍失效就暫停該模型，保存進度等待人工登入；其他模型繼續。
- 每模型清單結束後補抓失敗項目一輪。人工登入等待不消耗補抓輪次；明確續跑會為未完成項目開啟新一轮。
- 短暫通知在頁面變更時記錄，消失後仍可辨識。一般圖表及下載內容需符合 ticker 和模型；舊結果不直接當成成功。
- Table 原始表格沒有 ticker 標題，逐筆重載頁面、重選模型並輸入 ticker，等待新表格穩定後下載；驗證 Expiration、Gex、Dex 表頭及資料列，原始 HTML 內容不改寫。
- HTML 檔名仍為 `YYYY-MM-DD_HH;MM_TICKER_Model.html`；TV Code 為 `YYYYMMDD_TV Code.txt`，多行內容也支援去重和續跑。

限流額度及伺服器實際工作數無法從 Try Again 判定；等待上限代表本程式追蹤的請求數，並非伺服器已確認的工作數。

## 背景與排程入口

GUI、背景命令和既有排程使用同一個批次執行器。未指定視窗模式時採用儲存設定。沒有新增每日排程。

```powershell
.\LietaAutomator_1.2.0.exe --run-automated --multi-window --max-inflight 1
.\LietaAutomator_1.2.0.exe --run-automated --models Table
.\LietaAutomator_1.2.0.exe --resume-batch .\runs\batch_批次紀錄.json
.\LietaAutomator_1.2.0.exe --resume .\runs\舊版單模型紀錄.json
```

背景模式遇登入問題也會保留該視窗等待，不自動結束；登入後由另一個命令通知該模型重新檢查：

```powershell
.\LietaAutomator_1.2.0.exe --continue-batch .\runs\batch_批次紀錄.json --continue-model Table
```

`--no-multi-window` 保留單視窗模式；單視窗遇登入暫停時後續模型也需等待。`--max-inflight 2` 僅供明確選擇的對照測試。續跑不沿用先前的實驗上限，預設仍為 1。

程式透過 Windows 具名互斥鎖避免 GUI／排程重複操作固定偵錯埠（9222–9226），並保留與 1.1.x 的互斥相容。更早未使用此鎖的舊版須先結束。程序歸屬不符的偵錯埠會回報衝突，不以終止程序來解除。

回傳碼：0 全數完成；1 有未完成項目或使用者中止；2 初始化／設定失敗。登入暫停期間程序仍存活。批次紀錄包含各模型狀態、單模型紀錄位置及每次執行的提交、錯誤、逾時與耗時統計。

## 開發驗證

```powershell
python -m unittest discover -s tests -q
python tests/check_session_recovery.py -v
python tests/check_batch_browser.py -v
python tests/check_batch_timeout.py -v
python tests/check_background_chrome.py
python -m PyInstaller --noconfirm LietaAutomator.spec
.\dist\LietaAutomator_1.2.0.exe --self-check frozen-check.json
```

前兩個 Chrome 通知測試使用本機測試頁與獨立無介面 Chrome。`check_background_chrome.py` 會操作已登入的實站 Chrome 選單，但不提交模型请求。Chrome 整合測試可透過 `LIETA_TEST_CHROMEDRIVER` 指定已安裝驅動路徑。

Charles2.4 的 1／2 等待上限對照採独立輸出目錄，保留相同間隔及冷卻參數，比較成功率、每項重試次數和總耗時；不僅以速度判定是否適合切換。一次測試不能排除網站或 CBOE 連線波動。驗證 G 槽檔案也不等於驗證 Google Drive 遠端同步。

既有四模型的對照結果與驗證邊界請見 [1.1.0 驗證紀錄](docs/validation-1.1.0.md)。兩輪各 148/148，使用者接受等待上限 1 的 15 分 31 秒耗時；正式維持 1。Table 不包含在該次實站對照中。
