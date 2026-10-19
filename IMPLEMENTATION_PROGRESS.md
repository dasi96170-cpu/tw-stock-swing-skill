# 修復接續紀錄

## GitHub 交付（2026-10-19）

使用者要求先將目前成果提交並推送至 `origin/main`。本次交付包含下列各批修復、
測試及查核紀錄；實際推送是否完成，以 GitHub 分支與本次交付訊息為準。
下方各批「未 commit／push」描述保留當時歷史狀態，不代表本次交付狀態。
後續從「下一批」待辦接續，不要重做已完成的修復。

## 最新完成：S07 研究失敗隔離（2026-10-19）

- [x] Yahoo 請求失敗或 analyze 拒絕資料時，analysis=null、保留原因；官方資料與公司／公告仍可取得。
- [x] 明確市場按輸入路由；裸代號無 provider 身分時，查兩市場公司快照，以新鮮且唯一匹配路由。
- [x] 主檔歧義、失敗或過期時 UNRESOLVED，保留候選快照但不混合官方觀察或建立猜測的證券。
- [x] 各來源可預期網路／格式錯誤隔離；缺連接器不意外發出真實請求，非預期程式錯誤仍拋出。
- [x] 文字、JSON、SQLite 支援無技術報告；無分數／價位／虛構 Yahoo 觀察值，CLI 退出 4。
- [x] 保存失敗來源健康、路由依據、候選快照、coverage 與結論，沒有資料庫 schema 升級。

驗證：**131 項離線測試通過**（原 111 + 20）；`audit/degraded_research_tests.txt`。
S01–S09 九個診斷反例零重現：`audit/degraded_research_selection_results.json`；
A01–A09 原反例零重現：`audit/degraded_research_original_results.json`，兩腳本皆退出 0。
compileall／git diff --check 通過。本批沒有 live 請求、不宣稱任何來源目前可用，也未做績效回測。
診斷零重現只涵蓋指定案例，不代表官方日曆、公司行動、完整證券身分與新聞研究已完成。

API 契約變動：research.analysis 現在可為 null；消費端須檢查 analysis_error。
market_resolution 分 requested_market／provider_symbol／official_profiles／unresolved；
前兩者只表示路由，不是官方身分核驗。UNRESOLVED 的跨市場結果只保存在 supplements，不能當成已確認公司的證據。

下一批：官方交易日、個股停復牌／注意處置資料，減少初篩把正常休市誤排除的限制；
再接公司行動、歷史籌碼與初篩後深度研究。不要重做本批或放寬 P0 閘門。
未 commit／push；目前修改與證據保存在此工作區，沒有更改雲端設定。

## 前批完成：選股初篩安全修復批（2026-10-18）

使用者在查核後要求「那你繼續」，已實作本批，沒有 commit／push，沒有修改雲端設定。
規則與限制詳見 `SELECTION_POLICY.md`；矩陣 ID `REQ-SELECTION-001` 為 partial，不宣稱整個選股系統完成。

- [x] S01：成交金額估計中位數門檻、近 20 筆零成交排除；analyze／scan／research 一致阻擋。
- [x] S02：下跌或弱收盤的量值放大不作看多加分；不再用原股數量比排名。
- [x] S04：還原價格整欄缺失拒絕分析，保留部分缺漏防護。
- [x] S06：解析後市場代號去重、別名衝突排除、成功輸入與唯一證券數分列。
- [x] S09：同一官方列成交股數／金額／筆數零正矛盾隔離，不能當全數 available。
- [x] 部分防護 S03：中間平日缺列先排除，期間標觀察筆數；不是官方交易日完成。
- [x] 部分防護 S05：跨調整因子變動停用量值訊號，使用成交金額估計及價格確認；完整公司行動未接。
- [x] 部分防護 S08：provider 明確 TWD／EQUITY 才接受；官方 ISIN／類型核對未接。
- [x] S07：此批當時未修，已由上方 2026-10-19 批完成；以下驗證數字保留當時紀錄。

驗證：**111 項測試通過**（原 90 + 本批 21），`audit/selection_postfix_tests.txt`。
原 A01–A09 反例零重現：`audit/selection_postfix_original_regressions.json`。
新 S01–S09 診斷剩 S07 一項重現：`audit/selection_postfix_results.json`；退出 1 是已知未修項，
不是整個測試套件失敗。8 個原反例已防護不等於這 8 類完整解決，尤其 S03/S05/S08 還有上述限制。
compileall、git diff --check 通過。本批沒有 live 請求，不更新來源可用性結論；未執行多版本 CI／績效回測。

新增 `tests/test_selection_safety.py`；既有合成 fixture 補上明確還原價與幣別／類型，沒有刪除既有測試。
SQLite 的 conclusions.rationale_json 新增遮罩後技術分析快照／門檻／版本；無 schema 升級，
仍標記 full_inputs_replayable=false，不能宣稱能完整重算歷史報告。

副作用必須揭露：沒有官方日曆前，正常國定假日也可能因平日缺列被保守排除；
1,000 萬元是收盤×股數中位數的工程門檻，不是官方成交金額或已校準交易容量。

## 歷史查核補充：修復前快照

2026-10-18 使用者要求再次仔細查核，不是本輪修復授權。已新增 `SELECTION_AUDIT.md` 與
`audit/recheck_selection.py`，以合成案例重現 S01–S09 共 9 個剩餘弱點；正式程式未更動。
既有 90 項測試仍通過。證據 `audit/selection_recheck_results.json`、`audit/selection_recheck_tests.txt`。
此段為修復前狀態，請以上方最新修復批為準，原反例輸出保留不覆蓋。
第 2A 批資料接入完成不等於選股品質已驗證，勿因舊清單已打勾忽略本次發現。

## 第 2A 批已完成：公司主檔與官方重大訊息 metadata

- [x] 驗證上市／上櫃四個新端點，保存欄位／日期／雜湊，不保存公告全文。
- [x] 新增 12 個細項契約、來源與連接器；公告使用事件列表而非單值覆蓋。
- [x] 研究報告與 SQLite v3 保存來源、取得／發布／事件時間及缺口。
- [x] 測試空清單、缺日期、未來公告、重複／更正、錯公司、失敗與接續資訊。

這批不等於完成第 2 批全部內容；交易日、處置、歷史籌碼與完整資料目錄仍需接續。

### 本批驗收（2026-10-18）

- 完整測試 **90 項通過**：原 48 + 第 1 批 23 + 本批 19。紀錄 `audit/batch2a_tests.txt`。
- A01–A09 反例零重現：`audit/batch2a_reproduction_results.json`；compileall、git diff --check 通過。
- 2330／6488 主檔各六欄實測成功；其公告快照無匹配，不假裝沒有事件，命令退出 3。
- 另以實際快照 2072／4747 公告各一筆驗證全部六項 metadata；結果
  `audit/announcement_live_samples.json`。原始格式探索見 `audit/company_source_shapes.json`。
- `research 2330 --no-persist --json --timeout 8` 退出 4，兩個補充來源已接入，64 項 coverage
  完整列出；資料不足時交易價位仍為 null。證據 `audit/batch2a_live_research.json`。
- SQLite 落庫、證據關聯、無全文留存與 v2→v3 可重複升級以離線整合測試驗證。
- README、SKILL、REQUIREMENTS、SOURCE_CATALOG／VALIDATION、TRACEABILITY 已同步。
- 沒有修改雲端設定，沒有 commit／push；成果目前在此工作區。

邊界：公司六欄不是完整公司身分；公告不是新聞全文／利多利空分析，不能證明沒有重大事件。
出表日採三個日曆日預算而非官方交易日；事件鍵非官方 ID；條款、長期保存及再散布權仍待複核。

使用者已授權依 AUDIT_REPORT.md 修改。基準 f0ba29b003dc9b28e119611032a34321c8be641e。
下方保留第 1 批 A01–A09 歷史驗收。原查核報告／原始結果保留，不覆蓋。

## 工作清單

- [x] A01 還原價格報酬；A02 未來／歷史截止時間；行情數值驗證。
- [x] A03 有限數值；A09 官方資料一致性與表格長度驗證。
- [x] A04 缺漏與時間狀態分開；所有 P0 欄位共用閘門。
- [x] A05 SQLite 時區正規化與既有資料相容。
- [x] A06 掃描部分失敗；A07 所有輸出入口決策契約。
- [x] A08 Yahoo 身分核對與缺列揭露。
- [x] 正式回歸測試、完整測試、更新反例執行器與文件。

目前驗證：71 項測試通過（原 48 + 新 23）；修後反例 9/9 不再重現，證據為
`audit/postfix_reproduction_results.json`。完整測試紀錄：`audit/postfix_tests.txt`。
Python 3.12.14；compileall、git diff --check 通過。未跑多 Python 版本 CI／績效回測。

## 第 1 批交付狀態

已完成，未 commit／push。原查核證據沒有覆蓋。README、SKILL、REQUIREMENTS 及
TRACEABILITY 已同步；其中追蹤 ID `REQ-AUDIT-001` 涵蓋 A01–A09。

### 實測限制（不是已修復）

- `audit/postfix_live_sources.json`：sources --live --timeout 8 退出 2；TWSE 行情、TPEx 法人與
  TPEx 融資券共 3 個 ok；TWSE 融資券 partial（無可驗證日期）；TPEx 行情非 JSON；TWSE 法人 Tunnel 403。
- `audit/postfix_live_research.json`：research 2330 --json --no-persist --timeout 8 退出 4，
  insufficient、decision_eligible=false，入場／目標等為 null。
- 目前執行個體沒有允許 www.twse.com.tw，不繞過政策、不假裝已修復網路。
- 未重新驗證任何新新聞／財報／美股來源，也沒有提高既有來源的驗證等級。

重要實作界線：A02 採拒絕未來資料，不宣稱已能 PIT 回測；A05 新資料統一 UTC，舊資料以
SQLite 函數正規化讀取，未改寫既有證據。官方休市日、現金流／新聞／美股等仍未接入。
品質缺漏的日線採保守拒絕；尚未建立官方交易日與無交易日分類，不會自行插補行情。
SQLite 以函數正規化舊時間字串，避免改寫證據；大型資料查詢效能、舊資料無效時間清理與
歷史重複等價時間的衝突處理，需另以備份／遷移方案驗證。

## 後續批次（未完成，不可誤標完成）

2. 完整細項目錄、公司主檔／交易日／事件／歷史籌碼、快取備援與可重現證據。
3. 財報營收、新聞、美股、供應鏈、產業模板與多空研究流程。
4. 交易規則、成本與 PIT 樣本外回測、報告比較與失敗復盤。

## 中斷後

先讀本檔與 AUDIT_REPORT.md，檢查 git status。保留所有原查核證據與未提交修改。
第 1 批已完成，不需重新實作。執行 `PYTHONPATH=src python -m unittest discover -s tests -v` 與
`PYTHONPATH=src python audit/reproduce_findings.py` 核對現況；兩者目前都應退出 0。
第 2A 批、初篩安全批與 S07 隔離已完成，不需重做四個連接器。下一項第 2B 批核對官方交易日、注意／處置與停牌
資料的來源及契約，補時間與交易風險檢查，再接歷史籌碼、快取／備援與其餘細項目錄。
逐來源確認日期、單位、適用性及授權。不要把 official API 目錄列有端點當成已取得資料，
也不可放寬 P0 閘門換取答案。新增四個端點用 company 指令驗證，sources --live 仍只查原六個。
後續新聞、美股、供應鏈、回測需求仍在 AUDIT_REPORT.md、REQUIREMENTS.md，不得遺漏。
目前未 commit／push，保存位置是本工作區；額度用完不能背景執行或自動恢復。換環境前需備份。
