# 需求追蹤矩陣

機器可讀版本是 [`TRACEABILITY.toml`](TRACEABILITY.toml)。測試會驗證矩陣引用的程式與資料項目確實存在。

| 需求 ID | 需求 | 實作 | 驗證 | 狀態 |
|---|---|---|---|---|
| REQ-DATA-001 | 23 大資料族群不可遺漏 | `DATA_CATALOG.toml`、`catalog.py`、`coverage.py` | 23 個 category gate 與完整 ID 集合測試 | 已完成第一版 |
| REQ-DATA-002 | 行情、成交、法人、融資券欄位契約 | `DATA_CATALOG.toml`、`sources/official.py` | 六組 fixture contract 測試 | 已完成第一版 |
| REQ-SOURCE-001 | 官方來源、端點與可用性可追蹤 | `SOURCE_CATALOG.toml`、`sources/official.py` | 6/6 live schema 通過；來源失敗逐欄揭露 | 已完成第一版 |
| REQ-TIME-001 | Point-in-time 與修正版不可覆蓋 | `storage.py` | append-only 與 as-of 測試 | 已完成第一版 |
| REQ-EVIDENCE-001 | 證據帳本及品質維度 | `storage.py` | 品質七維保存測試 | 已完成第一版 |
| REQ-COVERAGE-001 | 每項恰有一個合法狀態 | `coverage.py`、`storage.py` | 漏項補狀態、未知/重複拒絕、P0 嚴格閘門 | 已完成第一版 |
| REQ-COMPAT-001 | 不破壞既有 analyze/scan | `cli.py` | 原有 CLI 與分析測試 | 已完成 |
| REQ-FUTURE-001 | 公式、回測、制度、安全、法律等深化 | `REQUIREMENTS.md` §14 | 後續逐項建立量化驗收 | 待辦 |

## 「不可靜默漏項」如何成立

1. `DATA_CATALOG.toml` 是欄位全集，先用 23 個 `category_gate` 鎖住需求大類。
2. 連接器必須宣告 `contract_fields`，且每一項都要回傳 observation 或 coverage 狀態。
3. `complete_coverage()` 以目錄為母集合；沒被提供的項目仍輸出 `missing`，原因為 `not_implemented` 或 `not_returned_by_source`。
4. 未知 ID、重複 ID、非法狀態直接失敗。
5. 已實作的 P0 欄位若為 missing、stale、failed、conflicting 或 unverified，嚴格閘門拒絕通過。
6. SQLite `coverage(run_id, field_id)` 主鍵保證每次研究每個項目最多一筆狀態。

目前全集包含 23 個族群閘門與第一批官方連接器的欄位級契約。後續展開財報、營收、新聞等族群時，必須先增加目錄 ID、矩陣映射與測試，再加入程式。
