# 官方來源驗證紀錄

最近驗證時間：2026-10-16 20:24（Asia/Taipei）

## 結果

| 連接器 | 官方端點 | Live 結果 | 契約欄位 |
|---|---|---:|---:|
| twse_market | `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | 通過 | 10/10 |
| twse_institutional | `www.twse.com.tw/rwd/zh/fund/T86` | 待新網域發布 | 0/9 |
| twse_margin | `openapi.twse.com.tw/v1/exchangeReport/MI_MARGN` | 通過 | 10/10 |
| tpex_market | `www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes` | 通過 | 10/10 |
| tpex_institutional | `www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading` | 通過 | 9/9 |
| tpex_margin | `www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance` | 通過 | 10/10 |

`www.twse.com.tw` 已加入環境設定草稿，但草稿尚未發布；目前 runtime 對該網域的 403 是白名單限制，不是官方服務失效。其餘五個端點均已取得真實資料並找到測試股票。

## 本次由 live 資料發現並修正

- 櫃買融資券原端點錯誤，已依官方 Swagger 改為 `tpex_mainboard_margin_balance`。
- 櫃買法人實際使用含空格與長名稱的英文欄位，已加入精確映射。
- 證交所 OpenAPI Swagger 已不再列出舊 `/fund/T86`，改用證交所主站的官方 RWD JSON。
- TWSE T86 是 `fields` 加二維 `data` 的表格式 JSON，解析器現可正規化為紀錄。
- 自營商買進及賣出由「自行買賣」與「避險」合計；買賣超仍讀官方總計。
- 行情與法人資料中的民國年日期會轉成 `Asia/Taipei` 時區時間。
- 官方大型 JSON 偶爾會截斷，HTTP 層現使用 gzip 並最多重試三次。
- 融資券空白數值欄依官方表格語意正規化為零；欄位根本不存在時仍標為 `missing`。
- 融資券數值單位更正為 `trading_units`，不再錯標為 shares。

## 實際抽樣

- TWSE 行情：2330，資料日期 2026-10-15，10/10 欄位。
- TWSE 融資券：2330，10/10 欄位。
- TPEx 行情：6488，資料日期 2026-10-16，10/10 欄位。
- TPEx 法人：6488，資料日期 2026-10-16，9/9 欄位。
- TPEx 融資券：6488，資料日期 2026-10-15，10/10 欄位。

## 尚待完成

發布新增 `www.twse.com.tw` 的環境草稿後，執行：

```bash
PYTHONPATH=src python -m tw_stock_swing sources --live
```

只有六個連接器全部取得合法 JSON、找到測試代號，且每個契約欄位都有明確狀態後，才能把本批官方來源標記為全面 live 驗證完成。
