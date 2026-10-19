# 台股波段研究專案查核接續紀錄

更新：使用者已授權修復，最新實作進度請讀 `IMPLEMENTATION_PROGRESS.md`。以下為原查核歷史，不代表修復狀態。

日期：2026-10-17。範圍：只查核、驗證與保存證據；不修改交易策略、不推送 GitHub。
查核基準：`f0ba29b003dc9b28e119611032a34321c8be641e`（開始時工作目錄乾淨）。

## 進度

- [x] 讀取需求、追蹤矩陣、來源驗證文件；確認雲端執行環境。
- [x] 初查行情供應器、技術指標與打分／交易計畫。
- [x] 重跑既有完整測試：48 通過；compileall 通過。
- [x] 用獨立反例驗證數值、時間與證據問題：9 個反例重現 9 個問題。
- [x] 重跑六個官方來源：3 ok、1 partial、2 failed；已區分環境限制、缺日期及非 JSON／逾時。
- [x] 核對研究流程、SQLite、CLI 與全部 23 類資料；取得兩交易所官方 API 目錄。
- [x] 真實 research 2330：insufficient、decision_eligible=false、退出 4，交易價位遮罩正確。
- [x] 完成 AUDIT_REPORT.md：缺陷分級、23 類缺口、消息／美股取得方案與四批驗收標準。

## 本輪查核已完成，尚未修復

成果入口：`AUDIT_REPORT.md`。所有證據位於 `audit/`。

- `baseline_tests.txt`：既有測試 48/48 通過。
- `reproduce_findings.py` / `reproduction_results.json`：離線反例；目前退出 1 是有意表示缺陷仍在。
- `live_first_check.json`：第一次六端點實測摘要，明確標為命令輸出轉錄。
- `inspect_live_shapes.py` / `live_shapes.json`：額外格式與官方 API 目錄診斷，沒有新增正式連接器。
- `research_live.json`：真實端到端研究輸出，使用 --no-persist，未寫既有研究資料庫。

下一個合理步驟：使用者授權修復後，先執行報告第 1 批。先核對基準版本，將 A01–A09 反例轉正式回歸測試，再修正式程式。尚未進行策略回測、新聞／新資料源接入或全市場長期驗證。

## 已確認環境限制

目前執行個體自訂允許網域為 openapi.twse.com.tw、www.tpex.org.tw、query1.finance.yahoo.com；不含 www.twse.com.tw。畫面上的新設定不代表本執行個體已套用。不得繞過網路限制。

## 接續方式

請讀取本檔、AUDIT_REPORT.md（若存在）與 audit/ 下的證據，先檢查 git status 和 HEAD。從尚未完成的項目接續，不能把未執行測試寫成通過。這次僅審查；未經新指示不改正式程式、不 commit／push。

額度中斷時不能背景繼續或自動恢復。上述檔案保存在本工作區，尚未推送；換環境或刪除環境前需另行保存。
