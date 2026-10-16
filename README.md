# 台股短線波段分析 Skill

這是一個「Codex Skill + Python 分析引擎」的台股短線決策輔助工具。Python
負責取得日線資料、計算指標與產生一致的交易計畫；Codex Skill 負責把結果整理成
清楚的繁體中文回答。

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

## 快速開始

本專案只需要 Python 3.11 以上版本，執行時沒有第三方套件依賴。

```bash
python3 -m pip install -e .
tw-stock-swing analyze 2330
tw-stock-swing analyze 6488 --json
tw-stock-swing scan --limit 5
tw-stock-swing scan 2330 2317 2382 3231
```

不安裝也可以直接執行：

```bash
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

## 資料來源與限制

目前日線資料透過 Yahoo Finance Chart API 取得。代號未指定市場時，程式會先嘗試
上市 `.TW`，再嘗試上櫃 `.TWO`。這個端點適合原型及個人研究，但不是交易所官方
即時報價，也可能受限流、延遲或調整。架構已將資料來源隔離在 `providers.py`，後續
可替換成正式授權來源。

內建清單不是全市場名單；它只是讓 MVP 能立即進行代表性掃描。若要掃描完整上市櫃
市場，請提供自訂代號檔，並留意大量公開 API 請求可能被限流。正式自動化版本應加入
官方證交所／櫃買中心股票清單、歷史資料快取及增量更新。

## 開發與測試

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

核心測試使用固定的合成行情，不依賴網路，因此可以穩定驗證計算與策略行為。

## 專案結構

```text
SKILL.md                       Codex Skill 操作與回答規範
src/tw_stock_swing/            Python 分析引擎及 CLI
src/tw_stock_swing/default_watchlist.txt  MVP 預設觀察清單
tests/                         離線單元測試
```
