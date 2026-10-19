# 台股短線波段分析 Skill

這是一個「Codex Skill + Python 分析引擎」的台股短線決策輔助工具。Python
負責取得日線與交易所官方資料、計算指標、揭露資料缺口並產生一致的交易計畫；
Codex Skill 負責把結果整理成清楚的繁體中文回答。

> 本專案僅供研究與決策輔助，不構成投資建議，也不保證獲利。行情可能延遲，實際
> 下單前請核對券商報價與重大資訊。

## 第一版能力

- 分析上市（`.TW`）及上櫃（`.TWO`）股票
- 計算 SMA、EMA、RSI、MACD、ATR、量比、20 筆觀察間隔動能與突破狀態
- 產生 0–100 分、候選／觀察／避開結論及可解釋的加減分理由
- 計算技術面參考價位；只有完整研究通過決策閘門時才對外顯示交易計畫
- 掃描內建 30 檔觀察清單或自訂股票清單（不代表當下流動性已驗證）
- 支援人類可讀文字及 JSON 輸出
- 偵測資料筆數與新鮮度，不用不足或過期資料硬做結論
- `research` 整合技術分析、官方行情、三大法人、融資券、來源健康與 23 類 coverage
- 官方來源部分失敗時保留已完成分析，但輸出 `insufficient` 並禁止正式交易計畫
- 排除盤中未完成日 K，以還原收盤價計算歷史指標，並比對同日官方與第三方收盤價
- 每次 `research` 預設保存到 SQLite，結論可回溯 coverage、觀察值與證據
- 取得上市／上櫃公司主檔與官方重大訊息快照的標題、發布時間、事件日期及版本雜湊（不含全文研究）

## 快速開始

本專案只需要 Python 3.11 以上版本，執行時沒有第三方套件依賴。

```bash
python3 -m pip install -e .
tw-stock-swing research 2330
tw-stock-swing research 6488 --json
tw-stock-swing analyze 2330
tw-stock-swing analyze 6488 --json
tw-stock-swing scan --limit 5
tw-stock-swing scan 2330 2317 2382 3231
```

不安裝也可以直接執行：

```bash
PYTHONPATH=src python3 -m tw_stock_swing research 2330
PYTHONPATH=src python3 -m tw_stock_swing analyze 2330
```

掃描自訂清單（每行一個代號，`#` 後為註解）：

```bash
tw-stock-swing scan --symbols-file my-watchlist.txt --limit 10
```

## 結果解讀

新初篩安全規則見 [`SELECTION_POLICY.md`](SELECTION_POLICY.md)：低流動性、近 20 筆零成交或
中間平日缺列未核實者標為「不納入」，即使分數高也不進排名。流動性採收盤×股數的估計值，
近 20 筆中位數預設至少 1,000 萬元，非官方成交額或已校準策略。無官方日曆時，正常假日也可能
保守排除；不代表真的缺交易日。還原價全缺拒絕分析、原股數量比不單獨加分或作排序鍵。
Yahoo 入口目前限明確 TWD／EQUITY，ETF 與未知類型不適用。`analyze` 初篩不合格退出 4；
`scan` 有排除退出 3，JSON 列出 `excluded_screening`、`duplicates` 及 `unique_succeeded`。

- `候選`：分數至少 70，技術條件較完整，但仍須核對新聞、基本面與大盤風險。
- `觀察`：分數 55–69，僅表示技術條件分數，未驗證扣成本後風險報酬。
- `避開`：分數低於 55，不代表一定下跌，只是不符合目前策略。

技術價位依最新完整日線及 ATR 計算，不是即時委託價。`research` 只有在所有 P0 類別、
資料時效、來源一致性及來源健康都通過時才顯示；目前仍有未實作 P0 類別，因此會安全地
隱藏價位並回報阻擋原因。預設策略偏向約 3–15 個交易日的短線波段。

`analyze` 與 `scan` 永遠屬於 `scope=technical_screen_only`、`decision_eligible=false`，
即使日線新鮮，也不在文字或 JSON 顯示交易計畫。JSON 保留價位鍵但值為 `null`。
`scan` 部分標的失敗或過期時回傳 `partial`／退出碼 3，全部失敗為 `error`／2。
成功退出 0 只代表技術篩選作業完成，不代表可以交易。

還原價格不得部分缺漏；Yahoo 丟棄的缺值／無效日線會揭露品質原因並阻擋分析。
包含截止日期之後的行情會直接拒絕，並非已完成歷史 PIT 回測。交易日目前仍是週末排除近似，
官方休市／颱風與逐來源公布時程尚待接入。

## 單股研究指令

`research` 是目前建議的單股入口：先以 Yahoo 六個月日線計算技術面，再依解析出的市場
自動選擇 TWSE 或 TPEx 的行情、三大法人、融資券、公司主檔與公告連接器。請求以有限併發
執行；結果包含每個數值的資料時間與來源、單日籌碼事實、決策閘門、來源健康，以及
`DATA_CATALOG.toml` 全部項目的明確 coverage。

Yahoo 失敗或日線品質拒絕時，研究不再整份中止：`analysis=null`、`analysis_error` 說明原因，
仍保留可取得的官方資料、coverage 與 SQLite 紀錄，退出 4／`insufficient`，不補造技術分數或價位。
明確輸入 `.TW`／`.TWO` 時按指定市場查詢；只輸入數字且 Yahoo 無法辨識時，查兩個市場主檔。
只有兩邊快照均可驗證時效且唯一匹配，才採用 `market_resolution=official_profiles`；
否則市場為 `UNRESOLVED`，保留候選快照，不混合行情／籌碼、不建立猜測的證券紀錄。
`requested_market` 僅表示使用者指定路由，`provider_symbol` 僅表示 provider 回傳路由，
都不等於完整官方 ISIN／轉板歷史驗證。可預期來源錯誤逐模組揭露，程式錯誤不被一律吞掉。

```bash
tw-stock-swing research 2330
tw-stock-swing research 6488.TWO --json
```

頂層 `status` 有三種：`complete` 才可形成正式決策；`partial` 表示資料可讀但仍有非致命
警告；`insufficient` 表示 P0 缺口、關鍵來源失敗、時間異常或跨來源衝突，正式入場、停損、
目標與風報比會設為 `null`。CLI 分別回傳 0、3、4，方便自動化流程阻擋不完整研究。
技術分數只是尚未校準的篩選分數，不是上漲機率，也不會因尚未回測的單日籌碼任意加減分。

本版仍不是完整深度研究：月營收、財報、估值、重大訊息內容分析、新聞、大盤與供應鏈尚未實作，
會在 `decision_blockers`、`warnings` 與 `coverage.missing_p0_categories` 中明列，不會假裝
已檢查。這也表示目前正常的 `research` 結果預期為 `insufficient`，直到那些 P0 模組完成。

研究預設保存至 `.tw-stock-swing/research.sqlite3`。可指定 `--database PATH`；只有測試或明確
不需要留存時才使用 `--no-persist`。資料庫保存研究 run、三時間觀察值、逐欄 coverage、
證據與結論的外鍵關聯；官方 payload 保存 SHA-256 與來源 metadata，不保存未授權全文。
時間輸入必須含時區，寫入統一 UTC；舊資料庫的時間字串在查詢時正規化，不改寫原始證據。
舊資料若含無時區或不合法時間會拒絕查詢，需另行核對來源後處理，不能猜測時區。

### 公司與公告快照

不依賴 Yahoo 的獨立查詢（只輸出 JSON，不寫入資料庫）：

```bash
PYTHONPATH=src python3 -m tw_stock_swing company 2330 --market TWSE
PYTHONPATH=src python3 -m tw_stock_swing company 6488 --market TPEx
```

公司主檔目前只有名稱、英文簡稱、產業代碼、掛牌日期、實收資本額與發行股數六項；
股數欄包含 TDR 原股語意，不能當成自由流通股數。公告只保存必要標題與時間／來源／版本 metadata，
不保存全文或聯絡人資料，不代表已閱讀全文、判斷利多利空或完成新聞搜尋。
快照沒有該股公告，不等於該公司沒有重大事件；`absence_proven` 永遠為 false。
同內容去重，更正版保留；事件鍵是欄位雜湊，不是官方事件 ID，改標題可能形成不同事件鍵。
目前以三個日曆日作快照時效預算，尚非官方交易日／公布時程驗證。
缺欄、無日期、未來日期與失敗都有逐欄狀態；命令退出 0／3／2 分別代表可解析完整／部分／失敗，
都不代表可以交易。`research` 自動納入這些資料，SQLite schema v3 以新增表保存每次快照，保留舊資料。

## 資料來源與限制

歷史日線資料透過非官方的 Yahoo Finance Chart API 取得，並明確列為第三方來源。代號未指定市場時，程式會先嘗試
上市 `.TW`，再嘗試上櫃 `.TWO`。官方單日行情、三大法人與融資券分別來自 TWSE 或
TPEx。這些來源都不是券商即時報價，也可能受限流、延遲、資料調整或暫時性服務錯誤；
實際下單前仍須核對券商報價與重大資訊。

內建清單不是全市場名單；它只是讓 MVP 能立即進行代表性掃描。若要掃描完整上市櫃
市場，請提供自訂代號檔，並留意大量公開 API 請求可能被限流。正式自動化版本應加入
官方證交所／櫃買中心股票清單、歷史資料快取及增量更新。

## 開發與測試

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

核心測試使用固定的合成行情，不依賴網路，因此可以穩定驗證計算與策略行為。
查核反例與修復回歸測試：`tests/test_audit_regressions.py`；本輪進度與剩餘批次見
[`IMPLEMENTATION_PROGRESS.md`](IMPLEMENTATION_PROGRESS.md)。測試通過不代表交易策略已有獲利能力。

## 研究資料基礎

第一批深度研究基礎已加入：

- [`DATA_CATALOG.toml`](DATA_CATALOG.toml)：23 大資料族群閘門及第一批欄位級契約。
- [`SOURCE_CATALOG.toml`](SOURCE_CATALOG.toml)：官方來源、端點、保存政策與驗證狀態。
- [`TRACEABILITY.toml`](TRACEABILITY.toml)／[`TRACEABILITY.md`](TRACEABILITY.md)：需求到程式與測試的映射。
- [`SOURCE_VALIDATION.md`](SOURCE_VALIDATION.md)：官方端點驗證證據與尚未完成項目。
- SQLite 三時間觀察值、證據帳本、coverage 狀態與 schema migration。
- 證交所／櫃買中心的行情、成交、法人及融資券六個連接器。
- 公司主檔與公告四個連接器；`sources --live` 仍只驗證原六個，新增四個用上述 `company` 命令驗證。
- 目錄共 64 項：23 個大類閘門與 41 個細項；大類仍為 6 個部分實作、17 個待辦，不能把目錄覆蓋率當成研究完成率。

```bash
PYTHONPATH=src python3 -m tw_stock_swing research 2330 --json
PYTHONPATH=src python3 -m tw_stock_swing catalog
PYTHONPATH=src python3 -m tw_stock_swing sources
PYTHONPATH=src python3 -m tw_stock_swing sources --live
PYTHONPATH=src python3 -m tw_stock_swing init-db research.sqlite3
```

目標系統、已完成範圍與後續不得遺漏的需求詳見 [`REQUIREMENTS.md`](REQUIREMENTS.md)。

## 專案結構

```text
SKILL.md                       Codex Skill 操作與回答規範
REQUIREMENTS.md                深度研究目標、資料範圍與驗收規格
DATA_CATALOG.toml              機器可讀研究欄位契約
SOURCE_CATALOG.toml            來源、端點與授權驗證狀態
TRACEABILITY.toml/.md          需求、程式與測試追蹤矩陣
src/tw_stock_swing/            Python 分析引擎及 CLI
src/tw_stock_swing/research.py 單股研究整合、來源健康與 coverage
src/tw_stock_swing/default_watchlist.txt  MVP 預設觀察清單
tests/                         離線單元測試
```
