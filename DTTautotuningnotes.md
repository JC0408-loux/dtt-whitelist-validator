# DTT 參數自動調校 + 自動跑分工具 — 開發交接筆記

> 這份筆記是為了**開新的聊天室繼續開發**而寫的。把整份貼給新的 Claude,
> 它就能在沒有本次對話紀錄的情況下接手。
>
> 建立日期:2026-09-08
> 相關既有專案:`jc0408-loux/dtt-whitelist-validator`(僅作為背景參考,新工具**不在**此 repo 下開發)

---

## 1. 目標

做一個**獨立於現有 repo 的新工具**,能夠:

1. 自動修改 Intel DTT(Dynamic Tuning Technology)的參數,特別是與 **EPO(`EPOT`)** 與 **IEOT** 相關的設定
2. 每套用一組設定後,自動跑指定的 benchmark
3. 迭代掃過多組參數組合,記錄每組的分數與當下實際套用的功耗狀態,找出最佳設定

換句話說:**parameter sweep / DOE(design of experiments)自動化跑分平台**。

---

## 2. 背景:既有的 `dtt-whitelist-validator` 是什麼

新工具會重用它的部分概念,所以先理解它。

### 2.1 它做什麼

驗證「啟動白名單中的應用程式時,DTT 有沒有切換到預期的 action set(`optimized_WL1` / `optimized_WL2`)」,並產出報表。

### 2.2 它怎麼讀 DTT(**這部分新工具可直接沿用**)

DTT 在 `http://localhost:8888/index.html` 有一個本機網頁,但那只是外殼。真正的資料流是網頁對
`ws://localhost:8888/echo` 開 WebSocket,送 ESIF 指令:

```
dptf ui getgroups                 -> Policies, Participants, ...
dptf ui getmodulesingroup 0       -> Adaptive Performance Policy, ...
dptf ui getmoduledata 0 0         -> 完整的 status XML(所有狀態都在這)
```

**全部都是唯讀指令。**

### 2.3 status XML 的關鍵結構

| 元素 | 意義 |
| --- | --- |
| `conditions_table` | 每個 action set 一筆,含每個 minterm 的 true/false |
| `actions_table` | `action_id` -> action set 名稱(如 `11` -> `optimized_WL1`) |
| `active_action` | 目前生效的 action set(第一個所有 minterm 皆為 true 的 row) |
| `conditions_directory` | 每個條件的即時值,含 `Workload` hint 與 `Power Source` |
| `request_directory` | **目前實際套用的 request:`PL1MAX`、`PL1MIN`、`IEOT`、`EPOT`** |

`EPOT`(EPO)與 `IEOT` 的值是 `optimized` / `performance` / `disabled` 這類狀態字串,
不是可直接寫入的數值參數。

參考實作:`dttwl/status.py`(XML 解析)、`dttwl/wsclient.py`(純標準函式庫的 RFC 6455 client)、
`dttwl/esif.py`(ESIF 指令封裝與 module 探索)。

### 2.4 幾個重要的設計決策(新工具應該沿用)

- **不開瀏覽器**:`msedge.exe` / `chrome.exe` 本身就在 workload-hint 白名單上,
  瀏覽器視窗取得 focus 會改變正在量測的狀態。所以直接走 WebSocket,不做 screen scraping。
- **穩定讀取(stable read)**:不是套用完 sleep 一下讀一次,而是持續 poll 直到
  「連續 N 次讀到相同狀態」才算數,避免抓到切換過程中的中間態。
- **重跑多輪抓 intermittent**:單次跑不出「有時候會、有時候不會」的問題。

### 2.5 它做不到什麼(**這就是新工具存在的原因**)

**它是純唯讀的。** 沒有任何寫入 / 設定 DTT 的能力。
決定「什麼條件觸發 EPO/IEOT、觸發後套什麼 PL1MAX/PL1MIN 曲線」的是
**OEM 燒錄的 DPTF policy table**,不在這個 repo 的管轄範圍。

---

## 3. 可行性結論(前次討論的結果)

修改 DTT 設定有**兩條路**,難度與持久性不同:

### 路線 A:即時覆蓋(不落地)

- 透過 ESIF 的寫入類指令(`setb` / `set config` / `setcfg` 之類,依 DTT/ESIF 版本命名不同)
  對執行中的 policy / participant 直接覆蓋參數
- Intel 隨 DTT 驅動包會附 `esif_uf_shell.exe`(ESIF UF Shell,命令列 console),
  OEM/ODM 工程師用它做即時實驗
- **重開機或 DTT 服務重啟就失效** → 適合 what-if 快速掃參數
- **對自動化跑分來說這條路比較實際**(每組設定套用快、不用重裝驅動)

### 路線 B:永久落地到 policy table

- 編輯 OEM 的 DPTF 設定表(通常是 `.dv` 或類似格式的 policy/conditions/actions table),
  再重裝 / reload 讓 DTT 服務重讀
- 需要 Intel 提供給 OEM 的 configurator 工具(**通常 NDA 下才拿得到**)
- 每組設定的套用成本高(要重啟服務甚至重裝驅動),掃參數會很慢

### 最大的前提風險

**路線 A 與 B 所需的寫入工具/權限,通常只有 OEM/ODM 工程團隊在 Intel 合作管道下才取得。**
如果拿不到,整件事做不了 — 這是要優先確認的事,不是寫程式能繞過的。
建議先問內部對接 Intel 的窗口(FAE / PDT)。

---

## 4. 開新 chat 前要準備的資訊(intake checklist)

### 4.1 DTT 寫入層存取(★ 沒有這個就無法開始)

- [ ] **ESIF 寫入指令的規格或範例** — 指令名稱、參數格式、回傳格式。
      如果有 `esif_uf_shell.exe`,把它的 help 輸出或幾個實際下指令的範例貼出來
- [ ] **走什麼通道** — 同一個 `ws://localhost:8888/echo`,還是另一個管道(named pipe / 別的 port / REST)
- [ ] **權限需求** — 需不需要 admin / 簽章 process / 白名單 caller 才接受寫入指令

### 4.2 Policy table(只有走路線 B 才需要)

- [ ] 目標平台目前的 **OEM DPTF policy table 原始檔**,或至少格式說明
- [ ] **編輯/產生這份表的工具**,特別是有沒有 CLI 模式(能不開 GUI 就跑)
- [ ] **套用新表後讓 DTT 重讀的步驟** — 重啟哪個 service、指令、要等多久才穩定
- [ ] 各參數的**安全範圍上限**(避免掃到會過熱降頻或觸發保護的組合)

### 4.3 平台資訊

- [ ] 目標機器的 **DTT/DPTF 驅動版本、EC/BIOS 版本**(不同版本指令集可能不同)
- [ ] 單一機種還是多機種?多機種的話各自 policy table 的差異

### 4.4 Benchmark

- [ ] 要用哪個 benchmark(Cinebench / 3DMark / PCMark / 自訂 workload)
- [ ] **它的 command-line / 無人值守跑法** — 啟動指令、分數輸出在哪個檔案、什麼格式
- [ ] 單輪跑多久、分數穩不穩定、要不要多輪取平均
- [ ] 要不要同時記錄溫度 / 功耗 / 風扇轉速?從哪讀
      (HWiNFO / Intel Power Gadget / EC log,或 `request_directory` 本身就夠)

### 4.5 參數空間與流程

- [ ] **要掃的參數有哪些、範圍多大** → 決定 grid search 還是取樣
- [ ] 想要的**輸出格式**(CSV / XLSX / 圖表 / 進內部 DB)
- [ ] 執行環境限制 — DTT 是 Windows 服務,腳本要跑在 Windows 上;
      能不能裝額外套件、有沒有網路

> **最少要有 4.1(或 4.2 擇一)+ 4.4 就能動手。** 其他可以邊做邊補。
> 如果 4.1 / 4.2 都還沒有,新 chat 應該先做「以現有權限能做到哪個層級」的評估,
> 而不是假設已經有 OEM 等級的存取權。

---

## 5. 建議的系統架構草稿

### 5.1 主迴圈

```
for candidate in parameter_space:
    1. apply(candidate)
       - 路線 A: 透過 ESIF 寫入指令即時覆蓋
       - 路線 B: 換 policy table + 重啟 DTT 服務
    2. wait_until_stable()
       - 持續 poll `dptf ui getmoduledata`,直到連續 N 次讀到相同狀態
       - 沿用既有 repo 的 debounce / stable_read_samples 概念
    3. verify(candidate)                     ← ★ 最容易被忽略、但最關鍵的一步
       - 讀 request_directory,確認 PL1MAX / PL1MIN / IEOT / EPOT
         真的等於這一組要測的值
       - 沒過就標記這組為 invalid,不要拿它的分數
    4. score = run_benchmark()
       - 同時背景記錄溫度 / 功耗
    5. log(candidate, score, actual_requests, thermals, timestamp, versions)
```

### 5.2 為什麼第 3 步不能省

如果沒有在跑分前確認「DTT 真的套到你以為的那組值」,量到的分數可能來自
完全不同的設定組合(被更高優先權的 action set 搶先、或寫入根本沒生效)。
整批數據會靜默地失效。這是既有 repo 已經解決的問題,直接沿用它的做法。

### 5.3 模組切分建議

| 模組 | 職責 | 可否重用既有 repo |
| --- | --- | --- |
| `dtt_read` | WebSocket + ESIF 讀取、status XML 解析 | ✅ 幾乎可直接搬 `wsclient.py` / `esif.py` / `status.py` |
| `dtt_write` | 套用參數(ESIF set 指令或換表 + reload) | ❌ 全新,取決於 4.1 的資訊 |
| `verify` | 讀回 request_directory 比對是否套用成功 | ✅ 概念沿用 stable-read |
| `bench` | 啟動 benchmark、解析分數 | ❌ 全新,取決於 4.4 |
| `telemetry` | 溫度 / 功耗背景記錄 | ❌ 全新(選配) |
| `sweep` | 參數空間定義、迭代排程、失敗處理 | ❌ 全新 |
| `report` | CSV / XLSX 輸出 | ✅ 可參考 `report.py` |

### 5.4 每組實驗要記錄的欄位

設定參數組合、benchmark 分數(每輪 + 平均)、實際套用的
`PL1MAX`/`PL1MIN`/`IEOT`/`EPOT`、`active_action` 名稱、溫度、功耗、
時間戳、平台/驅動版本、驗證是否通過。

---

## 6. 已知風險與注意事項

- **權限風險**:寫入層工具拿不到就整件事做不了 → 優先確認
- **熱/可靠度風險**:超出平台原始熱與功耗設計範圍的參數組合可能觸發 PROCHOT、
  降頻保護,長時間跑有熱可靠度疑慮。
  → 應在有溫度/功耗監控與**安全上限自動中止**的實驗室環境進行,不要在量產機盲掃
- **範圍限制**:部分功耗上限(ICCmax、VR loadline)是 **EC/BIOS 鎖的**,
  不在 DTT 管轄範圍。調 DTT 不會生效,要調得走 BIOS/EC 那層
- **時間成本**:每組設定 = 套用 + 穩定等待 + 跑分(可能數分鐘)× 多輪。
  掃 100 組可能要跑一整天 → 參數空間要先收斂,別一開始就全組合
- **失敗處理**:套用失敗、benchmark crash、中途過熱降頻,腳本都要能收尾並標記該組無效,
  而不是把壞掉的數據混進結果

---

## 7. 新 chat 的開場白範本

複製以下內容貼到新的聊天室(記得把 `<...>` 換成實際內容):

```
我要做一個 Windows 上的工具,自動迭代修改 Intel DTT 的參數(特別是 EPO/IEOT 相關),
每套用一組設定就跑一次 benchmark,最後找出效能最佳的設定組合。

背景筆記在此:<貼上這份 DTT-auto-tuning-notes.md 全文>

我目前手上有的資訊:
- DTT 寫入方式:<貼上 ESIF set 指令範例 / esif_uf_shell help 輸出 / 或寫「還沒有」>
- Benchmark:<benchmark 名稱 + command line 跑法 + 分數輸出位置>
- 要掃的參數:<參數名稱與範圍>
- 平台:<機種 / DTT 驅動版本 / BIOS 版本>

請先幫我確認以現有資訊能做到什麼程度,再開始設計架構。
```

---

## 8. 尚未決定的事

- 走路線 A(即時覆蓋)還是路線 B(改 policy table)— 取決於拿得到哪個工具
- 用什麼語言寫(既有 repo 是 Python 3.8+ 純標準函式庫 + openpyxl,沿用可省很多事)
- 參數空間的搜尋策略:全組合 grid search / 隨機取樣 / 或更聰明的(貝氏優化之類)
- 要不要做成 GUI,還是純 CLI 就好
