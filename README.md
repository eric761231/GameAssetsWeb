# GameAssets

天堂（Lineage）遊戲資源的比對與整理網頁。目前內容：

| 網頁 | 位置 | 說明 |
| --- | --- | --- |
| 381 → R 圖示對照 | `icon-compare/index.html` | 381 版道具、技能圖示和天堂R 依圖片編號對照；轉職技能（6000～6059）的 R 候選圖挑選；狀態圖示（effectlist2）依圖號對照，效果 ID／用途為輔助 |
| 圖示形狀比對 | `icon-compare/shape-compare.html` | 用圖片形狀比對 381 與 R 圖示：每張圖列出另一邊最像的前 3 名、同圖號排第幾；見下方「形狀比對」 |
| R 技能名稱 ↔ 圖示表 | `r-skills/r-skills.csv` | 天堂R 主動／被動技能名稱與圖號（`icon-compare/_build/rskills.py` 產生；2026-11-06 使用者改方向後未再擴充，保留現有產出） |

2026-11-06 從 GameLauncher 專案（`D:\程式碼測試區\C++\GameLauncher`）的 `artifacts\icon-compare\` 搬過來。

## 開網頁

直接用瀏覽器開 `icon-compare/index.html`（`file://` 即可，不需要伺服器）。
頁面讀同資料夾的 `data.js` 和 `img/items|skills|status/` 底下的 `381-<圖號>.png`、`R-<圖號>.png`。轉職技能分頁挑的圖可以從頁面匯出成 JSON。
381 圖在道具、技能與轉職技能頁以 31×31 顯示區保持比例呈現，不改變原始 PNG 尺寸；R 圖顯示尺寸不變。
道具、技能與狀態圖示頁可用固定於上方的首頁、上一頁、下一頁、末頁及頁碼輸入切換頁面。狀態圖示頁的搜尋可輸入圖號、效果編號或名稱文字。

`img/` 約 113MB，是建置產物，不進 git（見 `.gitignore`）；新 clone 下來要先跑一次建置。

## Netlify 部署

以專案根目錄作為發布目錄；根目錄的 `_redirects` 會將 `/` 重新導向至 `/icon-compare/`。若在 Netlify 設定了其他發布目錄，必須確保該目錄同時包含 `_redirects` 與 `icon-compare/`。

## Vercel 部署

以專案根目錄作為 Root Directory；`vercel.json` 會將 `/` 重新導向至 `/icon-compare/`。

Netlify 與 Vercel 的 Git 部署不包含被忽略的 `icon-compare/img/`，因此網頁可以載入，但圖片會回傳 404。建置圖片需要本機遊戲資源及 GameLauncher，兩者都無法僅從此倉庫重建圖片；如需在線上顯示圖片，須另外提供可公開發布的圖片來源及部署方式。

## 目錄

```
icon-compare/
  index.html               網頁
  shape-compare.html       形狀比對網頁（讀 shape-data.js）
  shape-data.js            形狀比對資料（shapematch.py 產生）
  data.js                  建置產生的資料（window.ICON_DATA）
  img/items/381-<圖號>.png  道具 381 圖
  img/items/R-<圖號>.png    道具 R 圖
  img/skills/381-<圖號>.png 技能／轉職技能 381 圖
  img/skills/R-<圖號>.png   技能／轉職技能 R 圖
  img/status/381-<圖號>.png 狀態 381 圖
  img/status/R-<圖號>.png   狀態 R 圖
  img/maps/R-<地圖編號>.png 天堂R主程式地圖縮圖（等角投影，跟遊戲同一套 24×12；最長邊 420）
  img/381/<圖號>.png       381 解包快取（Sprite*.pak 或 BMP 轉 png；舊路徑，建置不刪）
  img/r/<圖號>.png         R 解包快取（icon.idx 的 <圖號>.png；舊路徑，建置不刪）
  _build/
    build.py               建置腳本（入口）
    classskills.py         轉職技能分頁的資料與 R 候選圖配對（build.py 呼叫）
    statusicons.py         狀態圖示分頁：381 EffectList2.xml ↔ R effectlist2-c.xml 依圖號配對（build.py 呼叫）
    shapematch.py          形狀比對（獨立執行，輸出 shape-data.js）
    rskills.py             R 技能名稱 ↔ 圖示表（獨立執行，輸出 r-skills/）
    pb.py                  最小 protobuf 解碼（讀 R 的 item-common.bin）
    export-icon-pngs.exe   381 .tbt → png 的轉檔程式（建置產物，不進 git，編法見下）
    ids381.txt             建置中間檔：要匯出的 381 圖號（每次重建會覆寫，不進 git）
    skipped381skills.txt   建置報告：skill_id 超出 spellicon.idx 範圍、沒有納入的 381 技能
r-skills/
  r-skills.csv             R 技能名稱、職業、圖號、圖檔是否存在（UTF-8 BOM）
  _summary.txt             統計與缺名／缺圖清單
tools/
  export-icon-pngs.cpp     export-icon-pngs.exe 的原始碼
artifacts/shots/icon-compare/  網頁截圖
```

## 重建

需要 Python 3（套件 `brotli`、`cryptography`）。在 GameAssets 根目錄執行：

```
python icon-compare/_build/build.py                 產生 data.js 與 img/{items,skills,status}（解包快取已存在的圖會略過）
python icon-compare/_build/build.py --out <資料夾>  輸出到別的資料夾（例如從零重建來比對）
python icon-compare/_build/build.py --check          只檢查 data.js 引用的分類圖是否都在、三個資料夾裡有沒有沒被引用的圖
python icon-compare/_build/build.py --split-img      依現有 data.js 從 img/381、img/r 複製到三個分類資料夾（不解包）
python icon-compare/_build/build.py --import-assets  掃天堂R主程式全部地圖並畫縮圖，並複製 R 版 UI／立繪 PNG
python icon-compare/_build/build.py --import-bmp <資料夾>  匯入 LineageTool BMP（需要 Pillow；不覆蓋既有 PNG）
python icon-compare/_build/build.py --center-381    置中道具與技能頁引用的 381 PNG，並備份原圖（需要 Pillow）
python icon-compare/_build/build.py --style-preview 424 426 651 389 720 726 655 793 --style-output <新的輸出資料夾>
```

建置會用 `export-icon-pngs.exe` 匯出 381 圖到 `img/381`、從 R 的 `icon.idx` 解出 R 圖到 `img/r`（解包快取，已存在就略過），再依道具／技能／狀態引用複製到 `img/items`、`img/skills`、`img/status`，檔名加 `381-` 或 `R-` 前綴。同一圖號被多類引用就複製到多個資料夾。轉職技能圖算技能。`img/381` 未引用的圖會保留；`--check` 只掃三個分類資料夾。匯入 BMP 時依同名 INI 的 RGB555 `BackgroundColor` 將背景轉透明，INI 的偏移值不會改變圖片尺寸。數字加 `c` 的檔名保留原樣，不作為頁面的數字圖號。頁面資料列仍由任一側的道具或技能資料圖號產生，但兩側皆依實際圖檔顯示；該側同類資料未引用時名稱標為「未使用」，不表示此圖沒有其他用途。

`--center-381` 保留 PNG 原尺寸，將非透明像素的外框移到畫布中央；這不等同視覺重心，也會影響共用相同 PNG 的轉職技能圖。每張首次修改前的原檔存於輸出目錄的 `_build/381-originals/`，之後重複執行不會覆蓋備份。重新從零產生圖檔後須再執行此命令；備份目錄與圖片均不進 git。

`--style-preview` 是本機規則式試作，需 Pillow：讀取同圖號 381 和 R PNG，分別從 R 圖擷取圖案、光暈與高光參考色，將 381 圖的亮色區域轉為彩色圖案及光暈，輸出透明的 46×46 PNG 及三欄對照圖。它會去除原圖邊框與較暗細節，不會辨識語意或重畫圖案；同圖號僅供取色，不保證技能或狀態對應。必須以 `--style-output` 指定獨立的輸出資料夾，不會覆蓋現有檔案或來源圖；缺圖、無法取色或提取圖案時會報錯。

### 381 → R 風格分析與試作

- **工具**：[build.py](icon-compare/_build/build.py) 的 `--style-preview`，同時負責從 R 圖取色與產生轉換試作；目前沒有獨立的完整配色分析報告工具。
- **觀察**：檢視的 381 圖多為 32×32；R 樣本為 46×46，常見透明背景、亮色圖案與半透明彩色光暈。使用者提供的參考為 424 紅色、426 青藍色、651 亮黃色。
- **取色與轉換**：從同號 R 圖分別取彩色光暈、亮色圖案及高不透明度像素中最亮的 10% 作高光；再從 381 圖亮度擷取遮罩，去除邊緣、縮放並疊加光暈與顏色。各圖的取色結果會印在終端機。
- **限制**：亮度遮罩可能漏掉暗色細節、留下原圖背景，或無法重現 R 圖的輪廓與畫法。選取色不代表已辨識技能或狀態，也不表示同號圖片的內容必然對應；實際效果仍須人工核對。

| 結果 | 樣本圖號 | 說明 |
| --- | --- | --- |
| [亮度調整後對照圖](artifacts/style-preview-brighter/preview.png) | 424、426、651、389、720、726、655、793 | 最新試作：加強高光與光暈；同目錄有各圖的透明 PNG。 |
| [前一版配色對照圖](artifacts/style-preview-color/preview.png) | 同上 | 保留作亮度調整前的比較。 |
| [初版光暈對照圖](artifacts/style-preview-glow/preview.png) | 389、720、726、655、793 | 較早的試作結果。 |

在深灰背景上合成後，最新試作的第 99 百分位亮度比前一版提高；例如 651 為 247.7 → 255.0、655 為 234.2 → 255.0、793 為 233.2 → 255.0。此數值只衡量各輸出的高亮程度，不代表色彩、輪廓或整體風格已與 R 圖一致。

重跑時請將 `<新的輸出資料夾>` 換成沒有同名輸出的路徑，例如 `artifacts/style-preview-next`；既有結果不會被覆蓋。輸出包括每個圖號的 PNG 和 `preview.png`（381、R 參考圖、試作三欄），不會修改來源 PNG。

### 資料來源（全部唯讀，不會修改）

| 來源 | 預設路徑 | 環境變數 | 用到的東西 |
| --- | --- | --- | --- |
| GameLauncher 專案 | `D:\程式碼測試區\C++\GameLauncher` | `GA_GAMELAUNCHER` | `tools\re\re_tools.py`（R 封包 `rpak_entries`、XML 解密金鑰）；`src\` 給 export-icon-pngs 編譯用 |
| 381 客戶端資源 | `<GA_GAMELAUNCHER>\client` | `GA_CLIENT` | `spellicon.idx`、`Sprite.pak`／`Sprite00～15.pak`（.tbt 圖示）、`Data\EffectList2.xml` 散檔（沒有才讀 `Data.pak` 的）、`Text.pak` 的 `string-c.tbl`（DES 加密，狀態圖示名稱） |
| 381 Java 後端 | `C:\Users\eric7\eclipse-workspace\Lineage381C` | `GA_BACKEND` | `db\etcitem.sql`（etcitem）、`db\renewal_381.sql`（weapon、armor、skills）、`src\com\lineage\server\model\skill\L1SkillId.java`（BUFF_TRANSFOR_*） |
| 天堂R 客戶端 | `D:\天堂資料\天堂-主程式懶人包\天堂R\LineageRemastered` | `GA_RCLIENT` | `data.idx/pak` 的 `contents\item-common.bin`、`desc-c.tbl`、`string-c.tbl`、`SpellList-c.xml`、`passiveSpells-c.xml`、`effectlist2-c.xml`；`icon.idx/pak` 的 `<圖號>.png`；根目錄 `icon\` 散檔（icon.idx 沒有時才補用） |
| MapEditor | `D:\程式碼測試區\C++\MapEditor` | `GA_MAPEDITOR` | `mapnames.txt`（地圖名稱；沒有再查後端 `mapids`） |
| 天堂R 主程式地圖 | `<GA_RCLIENT>\map`、`Tile.idx`、`data.idx` 的 `zone3.xml` | `GA_RCLIENT` | 每一個 `map\<編號>\*.s32／*.seg` 畫縮圖到 `img/maps/R-<編號>.png`；R 名稱取 zone3 同編號第一個區域。381 名稱仍用 MapEditor `mapnames.txt`，沒有再查後端 `mapids`。點縮圖可單獨放大 |
| 天堂R UI 解包 | 桌面 `LineageTool_20230627\_PakOutput` | `GA_R_PAKOUTPUT` | `UI\**\*.png`（略過 Sample／FX_）、`Image\PO_*.png` 立繪 |

### 狀態圖示分頁怎麼對應（statusicons.py）

- 381：`client\Data\EffectList2.xml`（加密 XML，散檔優先，跟 GameLauncher `App::readDataPack` 同順序）。每個 `<effect id>` 的 `icon` 是 Sprite pak 的 `<icon>.tbt`（381 原生 `FUN_0045b340`），名稱是 `startMsg`＝N 指到 `Text.pak` 的 `string-c.tbl` 第 N+2 行（第 1 行是筆數）。381 伺服器送的狀態封包大多帶 `graphic`（`S_IconTime`）或效果編號，前端再查這份表找 `icon`；`spellicon.idx` 是技能視窗的圖，跟狀態圖示無關。
- R：`data.idx` 的 `effectlist2-c.xml`，欄位跟 381 相同（多 `tooltipMsg`、`gauge*`、`foodId` 等）；`icon` 是 `icon.idx` 的 `<icon>.png`，名稱查 R 的 `string-c.tbl`（同樣第 N+2 行）。R 客戶端用效果列建狀態圖示（LinR `016ae690`：列 +0x00 icon＝on 圖、+0x24 offIcon＝off 圖），但 R 伺服器的一般狀態走 `SC_SPELL_BUFF_NOTI`，圖號由封包直接給（見 GameLauncher `docs/re/r-status-icons.md`）。
- 配對（使用者 2026-11-06 改定、2026-11-07 放寬）：**以圖號為主**——381 狀態圖號 N（Sprite 的 `N.tbt`）和 R 狀態圖號 N（`icon.idx` 的 `N.png`）放同一列。使用者觀察兩邊大部分是同一張圖的新舊版，效果 ID／用途因為改版容易對不上。
  - 列集合＝兩邊效果清單用到的圖號聯集；每一側「有效果引用」或「圖檔存在」就顯示該側的圖（2026-11-07 放寬，跟道具／技能分頁一致：例如 R 沒有效果用圖號 1554，但 icon.idx 有 1554.png，就並排顯示，效果欄位留空並標「這一版的狀態沒用這個圖號」）。
  - 改號例外（2026-11-07）：建置時讀 GameLauncher `EditXml\RStatusIconMap.xml` 的 `<map icon381="N" r="M"/>`，381 圖號 N **直接改成** R 圖號 M 再配對（效果清單分組前改寫圖號；圖檔把 `img/381/N.png` 複製成 `img/381/M.png`（解包快取）與 `img/status/381-M.png`，原圖號保留，道具分頁等可能還在用），之後完全照一般規則、不另標記。目前 9 筆（481→5397、486→5395、3589→5231、3776→753、10000→8478、10001→8480、10021→8487、10023→8496、10024→8520）。改號目標圖號不從 381 原生 tbt 匯出（避免拿到不同內容的同號圖）；目標圖號若跟 381 道具／技能原生圖號衝突，建置會報錯。`<fallback>` 不影響本網站。
  - 每側列出「這一版用到圖號 N 的效果」（效果 ID、名稱、`startMsgName`），只當輔助；列上第一個效果直接顯示，全部效果在滑鼠提示裡。
  - 兩邊都有效果引用圖號 N 時：任何一個效果兩邊 `startMsgName` 相同 →「用途相同」，否則標「用途不同」（**不拆列**），可用「用途不同」篩選；任一側沒有效果引用、或該列是改號併列（用途本來就不同）時不標用途。
  - 每列上方印「圖號 N・對照依據：圖號・用途相同／用途不同」。
  - 圖案相似度這頁不算（形狀比對另有代理做 `_build/shapematch.py`、`shape-compare.html`）。
  - 搜尋：數字比對圖號或效果 ID，文字比對名稱、訊息、`startMsgName`。
- 效果 ID 兩邊是同一套（2026-11-06 抽樣：0 加速 `HASTE_S`、2 勇水 `SPEED_DOUBLE_S`、377 隱身 `BLIND_HIDING_S`、391 中毒 `CURSE_POISON` 等兩邊相同），所以用途比對用 `startMsgName` 有意義；但 R 改過約 30 個效果 ID 的用途。
- 圖：381 狀態圖跟道具／技能圖同一套 Sprite 編號，建置後放 `img/status/381-<圖號>.png`；R 狀態圖跟道具同一套 icon.idx 編號，放 `img/status/R-<圖號>.png`。解包快取仍在 `img/381/`、`img/r/`。
- 2026-11-07 建置結果：321 個圖號（9 筆改號直接改寫 381 圖號）；兩邊都顯示 262（兩邊都有效果引用 172：用途相同 160、用途不同 12——含改號後用途本來就不同的列）、只有 381 8、只有 R 47、兩邊缺圖 4。

R 解包快取 `img/r` 若放了建置不會產生的檔（例如有人把 `icon.idx` 整包解出，含 `<編號>_d.png`、`list`、`Thumbs.db`），建置會**略過清理解包快取**，不刪任何檔。`--check` 只掃 `img/items`、`img/skills`、`img/status`。舊路徑 `img/381/`、`img/r/` 若還在，建置不會刪。

天堂R 客戶端會自動更新，重建後 R 的道具名稱可能和舊的 `data.js` 有少數差異，這是正常的。

### 編 export-icon-pngs.exe

原始碼 `tools/export-icon-pngs.cpp` 用 GameLauncher `src\` 的 `PakFile`、`PakCrypto`、`SpriteSheet`、`Surface`（381 封包與 .tbt 解碼，和遊戲主程式共用，所以留在 GameLauncher）。在 GameAssets 根目錄執行（MinGW g++）：

```
g++ -std=c++17 -O2 -municode -DNOMINMAX -DUNICODE -D_UNICODE -I"D:/程式碼測試區/C++/GameLauncher/src" tools/export-icon-pngs.cpp "D:/程式碼測試區/C++/GameLauncher/src/PakFile.cpp" "D:/程式碼測試區/C++/GameLauncher/src/PakCrypto.cpp" "D:/程式碼測試區/C++/GameLauncher/src/SpriteSheet.cpp" "D:/程式碼測試區/C++/GameLauncher/src/Surface.cpp" -static -o icon-compare/_build/export-icon-pngs.exe -lwindowscodecs -lole32 -luuid -lgdi32 -lmsimg32
```

用法：`export-icon-pngs <381 client 資料夾> <圖號清單.txt> <輸出資料夾> <檔名前綴>`（build.py 會自動呼叫）。

## 形狀比對（shapematch.py、shape-compare.html）

2026-11-06 加入。圖號兩邊是不同編號體系、R 改版後用途也常對不上，所以直接用**圖片形狀**比對：每張 381 圖找 R 最像的前 3 名，每張 R 圖也找 381 最像的前 3 名。

### 怎麼跑

需要 Python 3 + Pillow + numpy（`pip install pillow numpy`）。先跑過 `build.py` 讓 `data.js`、`img/` 存在，再在 GameAssets 根目錄執行：

```
python icon-compare/_build/shapematch.py                 狀態、道具、技能全部比對，輸出 icon-compare/shape-data.js
python icon-compare/_build/shapematch.py --cats status   只比對某幾類（status,items,skills）
python icon-compare/_build/shapematch.py --validate      只印驗證組分數與隨機不同圖的分數分佈，不輸出
python icon-compare/_build/shapematch.py --check         只檢查 shape-data.js 引用的圖是否都在
python icon-compare/_build/shapematch.py --export-status-map <xml>  讀現有 shape-data.js，輸出 GameLauncher 的狀態圖示例外表（RStatusIconMap.xml）
```

- 只讀 `data.js`（取各分類兩邊用到的圖號與名稱）和 `img/`，不改它們、不複製圖。
- 候選池：381 是 `img/{items,skills,status}/381-<數字>.png` 全部（同號只算一次）；R 是 `R-<數字>.png` 全部，排除 `_d` 灰圖、`Thumbs.db`、`list` 等非 png。新資料夾沒有該圖時，退路讀舊的 `img/381/<數字>.png`、`img/r/<數字>.png`。
- 2026-11-06 耗時：讀圖算特徵約 70～100 秒（381 6259 張＋R 7187 張，讀不到或全空的 20 張略過），比對 381→R 3787 列＋R→381 4619 列共 5 秒（矩陣乘法向量化，分塊每次 256 列，全量比對不用先粗篩）。
- `--export-status-map`（2026-11-06）：GameLauncher R 介面以圖號為主，例外寫成 `EditXml\RStatusIconMap.xml`。規則：同號 < 70（或 R 無同號）且第一名 ≥ 85、與內容不同的下一名差 ≥ 2（或第一名是 R 狀態圖、下一名不是）→ `<map>` 改號；沒改號且同號 < 50 → `<fallback>` 畫 381（`STATUS_SAME_BY_EYE` 肉眼確認同圖的除外）；其餘門檻附近的寫成檔尾 `pending` 註解。細節見 GameLauncher `docs/plans/status-icons-r.md` 階段 8。
- `data.js` 改版（例如狀態圖改成以圖號對照）後重跑即可；腳本只取每列 `a`／`b` 的 `icon`（沒有就 `iconNo`）與名稱。

### 演算法（以形狀為主，顏色只佔 10%）

1. **去背、取前景強度圖**：先裁掉外圍全透明的邊。
   - 透明底的圖（道具、R 技能）：ink = alpha ×（0.3 + 0.7 × 亮度），暗色道具也留得住輪廓。
   - 有方框底的圖（381 狀態、381 技能、R 狀態）：R 狀態框內是半透明暗底，先乘 alpha；四邊各切 12% 去外框，取切完後最外 2 像素一圈的中位數色當底色，ink = 與底色的 RGB 距離——亮底暗紋、暗底亮紋都算同一個圖案。遮罩超過 60%（大片發光底、圖案碰到切邊，例：381 六芒星 5399）時在遮罩內再做一次 Otsu 取較亮的一層。
2. **遮罩、正規化**：遮罩 = ink > max(Otsu, 0.15)；裁到遮罩外框、補成正方形（置中、保持長寬比）、縮到 32×32。大小、位置不同的同一個圖案因此能對齊。
3. **特徵與分數**（0～100）：
   | 特徵 | 權重 | 算法 |
   | --- | --- | --- |
   | 遮罩容錯重疊 F | 0.35 | P = A 落在膨脹(B) 內的比例、R = B 落在膨脹(A) 內的比例（3×3 膨脹，容 1 像素偏移），F = 2PR/(P+R) |
   | 邊緣相關 E | 0.45 | ink 的 Sobel 梯度強度、模糊兩次後的皮爾森相關（負值當 0） |
   | pHash H | 0.10 | ink 的 32×32 DCT 左上 8×8（去直流 63 個）大於中位數為 1，相似度 = 1 − 2×漢明距離/63（負值當 0） |
   | 顏色 C | 0.10 | 前景的色相 12 格（飽和度加權）＋低飽和暗／中／亮 3 格，Bhattacharyya 係數 |
   權重是用驗證組做 AUC 格點搜尋挑的：E 最能分辨；F 對不同圖也常有 0.7～0.9，只能當輔助；pHash 對 381／R 發光畫法的差異敏感，所以壓低。
4. **門檻**：`SCORE_SAME = 70`（≥ 70 視為同一張圖；第一名 < 70 視為另一邊找不到相似圖）；`TIE = 2`（同圖號分數與第一名差 2 分以內算並列第一，R 常把同一個圖案存成多個圖號、只差顏色或細節）。

### 門檻依據（2026-11-06 `--validate`）

- 已知同圖（狀態圖同圖號、肉眼一致：加速 5397、勇水 5395、緩速 480、485、783、482、483、552、813、804、807、738、771、827、5241、890、1101、1125、1119、1558、1559、1110、5399、1477、1561）25 組：≥ 70 的 23 組（92%），中位數約 85；沒過的是 5399 六芒星 58.2（381 有大片發光底）、1110 箭頭 61.2（381 的細條紋在 32×32 被併掉）。
- 已知不同圖 12 組（735→5732、880、1609→9836 與不同狀態圖互比）：全部 < 70，最高 67.9（加速 5397 對緩速 480，同是橫向的圖案）。
- 隨機不同圖號 19999 組：中位數 35.8、P90 49.7、P99 64.1、P99.9 74.6；≥ 70 只有 0.3%（其中有些本來就是 R 的重複圖）。
- 參考（不拿來定門檻）：道具、技能在 R 多半重畫（構圖、比例不同），同物同圖號 14 組分數 46～88，技能多在 80 上下、道具常在 50～65。

### 怎麼看頁面（shape-compare.html）

- 開 `icon-compare/shape-compare.html`（`file://` 即可）。上方切換方向（381 → R／R → 381）、分類（狀態／道具／技能），篩選：
  - 同圖號不是第一：同圖號存在，但分數比第一名低 2 分以上；
  - 最高分低於門檻：前 3 名都 < 門檻，另一邊可能沒有對應圖；
  - 同圖號但不像：同圖號分數 < 門檻；
  - 高相似但不同圖號：第一名不是同圖號、≥ 門檻，而且同圖號沒有或 < 門檻（可能 R 改了編號）。
- 門檻可以在頁面上改（篩選即時套用；統計表固定用建置時的 70）。搜尋可打圖號（來源或候選）或名稱。網址可帶 `#cat=status&flt=renum`、`#dir=r`、`#5397` 直接打開。
- 每張卡片：左邊來源圖，右邊 3 個候選（圖號、分數、名稱），橘框 = 跟來源同圖號；分數 ≥ 門檻顯示綠色。
- 道具的「高相似但不同圖號」多半是同類造型（劍、靴、箭、寶箱），不是改號；道具同圖號幾乎都是重畫後的同一物品，形狀分數只能當參考。狀態圖的這一篩選比較有用（例：381 481 → R 5397、486 → 5395、3589 → 5231、10024 → 8520）。
