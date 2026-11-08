# AGENTS.md：給所有 AI 的共同規則

回覆使用者一律用**繁體中文**。這個專案的規則沿用 GameLauncher 專案的 `AGENTS.md`（`D:\程式碼測試區\C++\GameLauncher\AGENTS.md`），以下是和本專案有關的重點。

## 專案是什麼

天堂（Lineage）遊戲資源的比對、整理網頁（目前是 `icon-compare/`：381 → R 圖示對照）。使用方式與資料來源見 `README.md`。

## 絕對不能做的事

1. **遊戲客戶端與後端只讀不改**：GameLauncher 的 `client\`（尤其 `*.pak`、`*.idx`）、天堂R 客戶端、381 後端的 `db\`、`src\`，建置時只能讀。不要把整包客戶端資源複製進本專案。
2. **不改資料庫結構與資料**。要查資料只能用唯讀 `SELECT`。
3. **不 commit、不 push**，除非使用者要求。
4. **不要用猜的**：查不到的就標「未確定」，回報使用者。

## 固定做法

- 寫程式一律帶大括號與換行：`if`／`else`／`for`／`while` 後面一定加 `{}`，一行一個敘述，縮排 4 個空格（細節見 GameLauncher `docs/ai/role-braces.md`）。
- 原始碼用 UTF-8。遊戲文字資源是 Big5（cp950）。
- 網頁資料（`data.js`、`img/`）由 `icon-compare/_build/build.py` 產生，不要手改；改資料就改腳本再重建。
- 截圖放 `artifacts/shots/<功能>/`，一次性腳本不要留在專案裡。
- **不另外寫新的工具或一次性腳本**：需要新功能就加成現有腳本的參數或子指令。解包、解密沿用 GameLauncher 的 `tools/re/re_tools.py`，不要複製一份。
- GameLauncher `src\` 裡和遊戲主程式共用的程式（例如 `tools/export-icon-pngs.cpp` 用的 `PakFile`、`SpriteSheet`）留在 GameLauncher，本專案編譯時引用，不複製。
- 未決定、要使用者拍板的事，統一列成回覆表單給使用者。
