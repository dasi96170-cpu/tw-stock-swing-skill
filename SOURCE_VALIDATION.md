# 官方來源驗證紀錄

最近驗證時間：2026-10-17 10:38（Asia/Taipei）

## 結果

| 連接器 | 官方端點 | Live 結果 | 契約欄位 |
|---|---|---:|---:|
| twse_market | `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | 本次通過 | 10/10 |
| twse_institutional | `www.twse.com.tw/rwd/zh/fund/T86` | 本環境 tunnel 403；歷史 schema 通過 | 9/9（歷史） |
| twse_margin | `openapi.twse.com.tw/v1/exchangeReport/MI_MARGN` | 本次通過；來源日期仍須驗證 | 10/10 |
| tpex_market | `www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes` | 本次通過 | 10/10 |
| tpex_institutional | `www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading` | 本次通過 | 9/9 |
| tpex_margin | `www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance` | 本次通過 | 10/10 |

六個端點都曾取得真實資料、找到測試股票，且所有契約欄位都有明確狀態。2026-10-17
重查時 5/6 可連線且欄位完整；TWSE T86 由本環境網路隧道回覆 403，程式正確逐欄標為
`failed` 並以非零狀態結束。這是 payload
schema 抽樣驗證，不是永久可用性、正確性、授權或再散布權的保證。最後補齊的
TWSE 個股法人 T86 端點於 2026-10-16 20:53（Asia/Taipei）取得 2330 的 9/9 欄位，
因此本批官方來源的 live schema 驗證已完成。

完成第六個端點驗證的同一輪完整重查中，三個先前已通過的 TPEx 端點遇到 Cloudflare
`1016` origin DNS 暫時性錯誤。這是即時可用性波動，不推翻先前已保存的合法 JSON 與
完整 schema 驗證；`sources --live` 仍會在來源不可用時逐欄回報 `failed` 並以非零狀態結束。

## 本次由 live 資料發現並修正

- 櫃買融資券原端點錯誤，已依官方 Swagger 改為 `tpex_mainboard_margin_balance`。
- 櫃買法人實際使用含空格與長名稱的英文欄位，已加入精確映射。
- 證交所 OpenAPI Swagger 已不再列出舊 `/fund/T86`，改用證交所主站的官方 RWD JSON。
- TWSE T86 是 `fields` 加二維 `data` 的表格式 JSON，解析器現可正規化為紀錄。
- 自營商買進及賣出由「自行買賣」與「避險」合計；買賣超仍讀官方總計。
- 行情與法人資料中的民國年日期會轉成 `Asia/Taipei` 時區時間。
- 官方大型 JSON 偶爾會截斷，HTTP 層現使用 gzip 並最多重試三次。
- HTTP 層只重試暫時性網路／HTTP 錯誤，拒絕超過 25 MiB 的壓縮或解壓後內容，並為成功
  payload 保存 SHA-256；永久性 4xx 不再盲目重試。
- 融資券空白數值欄依官方表格語意正規化為零；欄位根本不存在時仍標為 `missing`。
- 融資券數值單位更正為 `trading_units`，不再錯標為 shares。

## 實際抽樣

- TWSE 行情：2330，資料日期 2026-10-15，10/10 欄位。
- TWSE 法人：2330，資料日期 2026-10-16，9/9 欄位。
- TWSE 融資券：2330，10/10 欄位。
- TPEx 行情：6488，資料日期 2026-10-16，10/10 欄位。
- TPEx 法人：6488，資料日期 2026-10-16，9/9 欄位。
- TPEx 融資券：6488，資料日期 2026-10-15，10/10 欄位。

## 重現與健康檢查

執行：

```bash
PYTHONPATH=src python -m tw_stock_swing sources --live
```

此命令會即時重查六個端點；任何端點失敗時都保留完整欄位契約、輸出失敗原因，並回傳
非零狀態。歷史 schema 驗證完成不代表官方來源永久可用。

`status=ok` 只表示本次所有契約欄位成功解析且回應含可驗證資料日期；欄位缺少或來源日期
不可驗證時為 `partial` 並回傳 3，
端點失敗時為 `failed` 並回傳 2。資料日期、交易日曆與跨來源一致性由 `research` 的決策
閘門另行判斷。官方條款、限流與長期保存政策仍需逐資料集完成法律及營運驗證。
