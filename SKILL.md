---
name: tw-stock-swing
description: 取得台股日線資料，以可重現的 Python 分析引擎篩選短線波段標的，並提供入場、停損、目標與風險說明。適用於使用者要求台股選股、個股技術分析、短線波段規劃或候選股比較時。
---

# 台股短線波段分析

以 repository 內的 Python 引擎為唯一數值計算來源，不要自行臆測行情、指標、價位或
即時資訊。

## 執行方式

先確認目前工作目錄是本 repository；每個雲端任務已在隔離環境中，使用現有 checkout，
不要自行建立 Git worktree。

個股研究（建議入口，整合官方行情、法人、融資券與 coverage）：

```bash
PYTHONPATH=src python3 -m tw_stock_swing research <股票代號> --json
```

僅做舊版技術分析：

```bash
PYTHONPATH=src python3 -m tw_stock_swing analyze <股票代號> --json
```

候選股掃描：

```bash
PYTHONPATH=src python3 -m tw_stock_swing scan --limit 10 --json
```

比較指定標的：

```bash
PYTHONPATH=src python3 -m tw_stock_swing scan <代號1> <代號2> <代號3> --limit 10 --json
```

## 回答規範

1. 檢查命令與 JSON：只有 `status=complete` 且 `decision_eligible=true` 才可提供正式交易計畫。
   `partial` 或 `insufficient` 只能整理已取得事實、技術篩選與缺口，不可補造答案或自行還原
   已被設為 `null` 的入場、停損、目標及風報比。
2. 明確列出研究時間、日線資料日期、官方資料日期及來源，若有 `warnings` 必須呈現。
3. 個股回答依序提供：研究狀態、資料阻擋原因、技術篩選分數、行情摘要、法人與融資券
   事實、理由、資料缺口與風險；只有決策合格時才列入場、停損與目標。
4. 多股掃描按分數排序，但不要把分數描述成上漲機率。
5. 說明所有價位是以日線及 ATR 計算的研究參考，不是即時委託建議。
6. 使用繁體中文、具體且簡潔。不可承諾獲利，不可把工具描述成持牌投資顧問。
7. 若使用者要求盤中即時結論，說明目前資料源是日線資料並要求先核對券商即時行情。

## 判斷邊界

- `候選` 只表示符合這套技術條件，不等同「一定要買」。
- 技術分數未經勝率校準，不得描述成機率、信心百分比或投資建議。
- 盤中當日日 K 尚未完成時必須排除；跨來源同日價格衝突或資料過期時不得提供交易計畫。
- 資料少於 60 根有效日 K 時，分析引擎會拒絕評分。
- 月營收、財報、估值、新聞、處置／注意股票、大盤及個人財務狀況尚未納入 `research`，回答時應列為限制。
- 單日法人與融資券目前只列為事實，不得自行改動技術分數或宣稱已建立可獲利規則。
- 使用者若要實際配置部位，先詢問可承受損失；未提供時只談價格風險，不推算股數。
- `research` 預設寫入 `.tw-stock-swing/research.sqlite3`；保留 run ID，讓後續研究能比較結論變化。
