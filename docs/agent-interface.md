# AI agent 操作方式（1.3.0，協定版本 1）

AI 透過短暫的命令程序發送指令；常駐 GUI 負責下載與顯示。監測程序結束不會停止下載。只有 GUI 持有瀏覽器互斥鎖，所有模型共用等待上限 1、至少 5 秒提交間隔及既有冷卻規則。控制入口只使用程式資料夾中的本機檔案，不需要網路服務。

## 準備任務

複製 `agent-plan.example.json`，將每份清單的 `tickers_file`、`destination`、`models` 改成實際設定。範例路徑不代表已建立資料夾。路徑需為絕對路徑；來源與目的地都必須存在，目的地須可寫入。JSON 及 ticker 檔案使用 UTF-8，可含 BOM。

每組 `id` 必須唯一，僅使用英數、底線或連字號；`name` 可以是中文。程式在任何下載前驗證所有任務，再保存 ticker 快照，續跑沿用該快照與目的地。下次排程使用新的任務 ID，會重新讀取清單。

各模型依序處理 jobs。某模型等待登入時，同模型後續清單保留，其餘模型繼續下一份清單；GUI 卡片顯示各自正在處理的任務。每份清單的每個模型完成既有內部補抓後，若仍未完成，自動額外重試一次；仍失敗就記錄並向後推進。登入等待不消耗這次額外重試。

## 啟動及查詢

以下路徑均為示例，執行時替換成真實位置。打包版沒有文字主控台，必須指定 `--output` 並讀取該 JSON；不能只憑命令結束認定下載完成。

```powershell
& 'C:\Lieta\LietaAutomator_1.3.0.exe' --agent start --plan 'C:\Lieta\daily-plan.json' --task-id daily-20261005 --request-id start-20261005 --output 'C:\Lieta\start-result.json'
& 'C:\Lieta\LietaAutomator_1.3.0.exe' --agent status --task-id daily-20261005 --output 'C:\Lieta\status-result.json'
```

若新版 GUI 未開啟，start／resume／retry 會嘗試獨立啟動 GUI。若系統不允許獨立啟動，請手動開啟新版再送指令。舊版正在執行時會被互斥鎖攔截，不會強制關閉。正常情況下，AI 每 10 秒查詢一次即可。

查詢重點：

- `connected`：GUI 是否在最近 8 秒更新狀態。false 表示離線或狀態過期，不能把舊狀態當成正在執行。
- `task.id`：必須符合預期的任務 ID。
- `task.terminal`：本次執行是否結束；`state: needs_login` 不代表結束。
- `task.states`：每模型目前 ticker、所屬清單、成功數與未完成數。
- `task.dispatch`：等待中請求數及共用冷卻倒數。
- `task.needs_login`：需要登入的模型。通知使用者登入；其他模型持續執行。
- `task.summary`：結束後列出總數及每項失敗的清單 ID、模型、ticker、原因、目的地。
- `next_action`、`poll_after_seconds`：後續操作提示。

未指定 task-id 時查詢目前 GUI；指定已保存但非目前執行中的任務時，回傳其保存紀錄。舊紀錄若仍標示 running／prepared，回覆 interrupted，不能據此聲稱程序仍存活。

## 登入、停止、續跑與額外人工重試

```powershell
& 'C:\Lieta\LietaAutomator_1.3.0.exe' --agent continue --task-id daily-20261005 --continue-model Term --request-id login-term-1 --output 'C:\Lieta\command-result.json'
& 'C:\Lieta\LietaAutomator_1.3.0.exe' --agent stop --task-id daily-20261005 --request-id stop-1 --output 'C:\Lieta\command-result.json'
& 'C:\Lieta\LietaAutomator_1.3.0.exe' --agent resume --task-id daily-20261005 --request-id resume-1 --output 'C:\Lieta\command-result.json'
& 'C:\Lieta\LietaAutomator_1.3.0.exe' --agent retry --task-id daily-20261005 --request-id retry-1 --output 'C:\Lieta\command-result.json'
```

continue 只通知指定模型驗證登入並接續，不會重啟其他模型。stop 通知所有工作停止新提交並保存；收到 accepted 後仍須等 terminal。不要直接終止 GUI 或 Chrome。

resume 用於中止或意外中斷後接續，不重置已用完的額外重試額度。已結束且耗盡重試的失敗項目會留在總結。retry 是明確要求再開啟一次補抓週期，重驗檔案、略過已完成項目；不要在自動完成後無限重送 retry。GUI 的「重試失敗項目」也能處理 AI 任務。

進度、補抓額度與來源快照存於 `agent_runs/<task-id>.json`，各模型使用既有 `runs` 紀錄。更換清單及自動補抓共用同一排程器，保留冷卻。若 GUI 意外中斷而留下未結束的紀錄，重啟後先保守冷卻 120 秒，避免假設伺服器已取消原請求。

## 防止重複操作

每個變更指令必須提供 request-id。相同操作重送時使用相同 ID，會回傳原確認結果；不同操作必須使用不同 ID。相同 task-id 不會再次建立下載任務。新一日或新一輪資料使用新 task-id。

命令接收與下載完成是兩件事。回覆 `ok: true`／`accepted: true` 只代表已接收，後續查詢狀態。如果回覆 `outcome: unknown`，先查詢該 task-id，不要立刻換新 ID 再送 start 或 retry。程式會在操作前保存請求紀錄；即使操作途中 GUI 中斷，也不會自動重播不確定是否已執行的操作。

agent_control 包含心跳、命令與回覆；agent_runs 和 runs 包含本機進度，不應刪除執行中資料或提交至 GitHub。AI 控制命令結束碼 0 代表控制操作成功／GUI 連線有效，2 代表錯誤或離線；下載結果一律查看 task.summary。

此版本沒有建立每日排程。Windows 睡眠、關機或未登入桌面時的啟動條件，需在日後設定實際排程時另行驗證。
