# 台股短線波段分析 Skill

這是一個「Codex Skill + Python 分析引擎」的台股短線決策輔助工具。Python
負責取得日線與交易所官方資料、計算指標、揭露資料缺口並產生一致的交易計畫；
Codex Skill 負責把結果整理成清楚的繁體中文回答。

> 本專案僅供研究與決策輔助，不構成投資建議，也不保證獲利。行情可能延遲，實際
> 下單前請核對券商報價與重大資訊。

## 第一版能力

- 分析上市（`.TW`）及上櫃（`.TWO`）股票
- 計算 SMA、EMA、RSI、MACD、ATR、量比、20 日動能與突破狀態
- 產生 0–100 分、候選／觀察／避開結論及可解釋的加減分理由
- 提供參考入場區、停損、兩段目標價與風險報酬比
- 掃描內建高流動性觀察清單或自訂股票清單
- 支援人類可讀文字及 JSON 輸出
- 偵測資料筆數與新鮮度，不用不足或過期資料硬做結論
- `research` 整合技術分析、官方行情、三大法人、融資券、來源健康與 23 類 coverage
- 官方來源部分失敗時保留已完成分析，明確輸出 `partial` 與失敗欄位

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

- `候選`：分數至少 70，技術條件較完整，但仍須核對新聞、基本面與大盤風險。
- `觀察`：分數 55–69，型態尚未完整或風險報酬不足。
- `避開`：分數低於 55，不代表一定下跌，只是不符合目前策略。

入場、停損與目標都是依最新收盤價及 ATR 計算的研究參考值，不是即時委託價。
預設策略偏向約 3–15 個交易日的短線波段。

## 單股研究指令

`research` 是目前建議的單股入口：先以 Yahoo 六個月日線計算技術面，再依解析出的市場
自動選擇 TWSE 或 TPEx 的行情、三大法人及融資券三個官方連接器。三個官方請求會並行
執行；結果包含每個數值的資料時間與來源、單日籌碼事實、交易計畫、來源健康，以及
`DATA_CATALOG.toml` 全部項目的明確 coverage。

```bash
tw-stock-swing research 2330
tw-stock-swing research 6488.TWO --json
```

頂層 `status` 為 `ok` 表示本版主動使用的四個來源都成功且日線沒有過期警告；`partial`
表示仍有可用研究結果，但至少一個官方來源失敗、缺欄位或任何主動資料源過期。兩種狀態
都會正常輸出報告；只有代號錯誤或連日線都無法取得時才以錯誤結束。技術分數目前不會因
單日法人或融資券數字自動加減分，避免尚未回測的籌碼規則製造假精確度。

本版仍不是完整深度研究：月營收、財報、估值、重大訊息、新聞、大盤與供應鏈尚未實作，
會在 `warnings` 與 `coverage.missing_p0_categories` 中明列，不會假裝已檢查。

## 資料來源與限制

歷史日線資料透過 Yahoo Finance Chart API 取得。代號未指定市場時，程式會先嘗試
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

## 研究資料基礎

第一批深度研究基礎已加入：

- [`DATA_CATALOG.toml`](DATA_CATALOG.toml)：23 大資料族群閘門及第一批欄位級契約。
- [`SOURCE_CATALOG.toml`](SOURCE_CATALOG.toml)：官方來源、端點、保存政策與驗證狀態。
- [`TRACEABILITY.toml`](TRACEABILITY.toml)／[`TRACEABILITY.md`](TRACEABILITY.md)：需求到程式與測試的映射。
- [`SOURCE_VALIDATION.md`](SOURCE_VALIDATION.md)：官方端點驗證證據與尚未完成項目。
- SQLite 三時間觀察值、證據帳本、coverage 狀態與 schema migration。
- 證交所／櫃買中心的行情、成交、法人及融資券六個連接器。

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
