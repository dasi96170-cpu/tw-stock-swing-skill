# 官方來源驗證紀錄

驗證時間：2026-10-16（UTC）

| 連接器 | 官方端點 | 離線格式驗證 | Live 驗證 |
|---|---|---:|---:|
| twse_market | `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | 通過 | 環境政策阻擋 |
| twse_institutional | `openapi.twse.com.tw/v1/fund/T86` | 通過 | 環境政策阻擋 |
| twse_margin | `openapi.twse.com.tw/v1/exchangeReport/MI_MARGN` | 通過 | 環境政策阻擋 |
| tpex_market | `www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes` | 通過 | 環境政策阻擋 |
| tpex_institutional | `www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading` | 通過 | 環境政策阻擋 |
| tpex_margin | `www.tpex.org.tw/openapi/v1/tpex_margin_balancing` | 通過 | 環境政策阻擋 |

實際連線測試均收到環境 egress proxy 的 HTTP 403，不能據此判定官方服務失效。已將 `openapi.twse.com.tw` 與 `www.tpex.org.tw` 加入雲端環境設定草稿；草稿須經審閱、儲存並發布後，新的 runtime 才能執行 live 驗證。

目前可確認來源為官方網域；六個端點的預期欄位別名、數字格式、代號比對、空值與缺欄處理都有離線 fixture 測試。尚未確認 live response 完整 schema、實際更新時間、歷史範圍、rate limit 與條款允許的保存細節。在完成前，`SOURCE_CATALOG.toml` 保持 `blocked_by_environment_policy`，不得宣稱已全面驗證。

發布環境設定後執行：

```bash
PYTHONPATH=src python -m tw_stock_swing sources --live
```

成功條件是六個連接器皆取得 JSON、找到測試代號，且契約中的每個欄位都有明確狀態。
