# DawnAssetHelper

DawnAssetHelper 是一個用於協助解包 Unity AssetBundle 遊戲資源的 Windows 桌面 GUI 工具，主要針對 Hero 資源（Spine 相關檔案）進行快速解包與分類。

![screenshot](https://github.com/Kevin-NM/DawnAssetHelper/releases/download/v1.0.0/2026-07-22.225849.png)

## 專案用途

1. 掃描指定目錄下的 `.ab` 檔案，自動根據命名規則辨識 Hero ID 與分類 (Original, Collab, Skin)。
2. 提供視覺化介面選擇需要解包的 Hero。
3. 自動呼叫 `AssetStudio CLI` 進行解包。
4. 在解包結果中自動匹配並整理 Spine 所需的 `.skel` / `.json`, `.atlas`, `.png` 檔案。
5. 產生完整的解包過程 `run.log` 與 `summary.json` 以便除錯與後續處理。

**重要限制聲明：**
- 本工具不提供破解功能。
- 不會繞過 DRM 或加密。
- 不內建任何遊戲素材。
- 所有的外部工具路徑均由使用者自行設定。

## 環境需求

- OS: Windows
- Python 3.11+
- [AssetStudioModCLI](https://github.com/Perfare/AssetStudio)

## Windows 使用者啟動方式（推薦）

1. 進入 DawnAssetHelper 專案資料夾。
2. 雙擊 `open.bat`。
3. 第一次啟動會自動建立 venv 並安裝 requirements。
4. 之後啟動會直接開啟 GUI。
5. 如果 `requirements.txt` 有更新，`open.bat` 會自動重新安裝依賴。
6. 如果出現找不到 Python，請安裝 Python 3.11+ 並勾選 Add Python to PATH。

## 手動安裝與執行方式

1. 確保已安裝 Python 3.11+
2. 建立虛擬環境 (建議)：
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```
3. 安裝相依套件：
   ```bash
   pip install -r requirements.txt
   ```
4. 執行主程式：
   ```bash
   python main.py
   ```

## 使用指南

### 1. 如何設定 AssetStudio
- 在 GUI 介面的「AssetStudio CLI 路徑」點選「瀏覽」，選擇您下載的 `AssetStudioModCLI.exe` 或是 `AssetStudioModCLI.dll` (需有 .NET 執行環境)。

### 2. 如何設定遊戲資源目錄
- 在「遊戲資源目錄」欄位點選「瀏覽」，選擇包含 Hero `.ab` 檔案的目錄。

### 3. 如何掃描 Hero
- 設定好遊戲資源目錄後，點擊「掃描 Hero」。系統會自動過濾並分類所有的 Hero AB 檔案。

### 4. 如何解包選取 Hero
- 在 Hero 清單中勾選您想要解包的項目（也可以透過「全選」等按鈕快速選擇）。
- 點擊「解包選取 Hero」，DawnAssetHelper 就會自動進行解包流程。
- 每個 Hero 都會產生一個獨立目錄，並進行資源正規化，最終整理出 Spine Pro 可用的標準三件套檔案。

### 5. 匯出結果結構
程式預設會將整理好的解包檔案存放在您設定的輸出根目錄（預設為 `./output`）下，結構大致如下：
```text
output/
  runs/
    20260504_120000/
      heroes/
        hero1120/
          matched/
            hero1120.skel    # 自動重新命名與標準化的骨架
            hero1120.atlas   # 自動重新命名與標準化的 Atlas
            hero1120.png     # 唯一的一張主貼圖
          unpacked/          # 拆圖後產生的小圖 (若有執行拆圖)
            head.png
            body.png
            arm.png
          raw_export/        # AssetStudio 原始解包檔案
          summary.json       # 該 Hero 解包成功與否的摘要
      logs/
        run.log          # 所有執行過程日誌
      summary.json     # 該次執行的總結報告
```
注意：AssetStudio 匯出時可能會得到 .bytes、.asset 等後綴，DawnAssetHelper 會自動整理成 Spine Pro 可用的標準檔名並只保留必要的三件套，濾除 AnimationClip 或 MonoBehaviour 等不必要的元件。

### 6. Spine Atlas 拆圖 (v0.2)
DawnAssetHelper 支援呼叫 `Spine.com` 將 `.atlas` 與 `.png` 拆解回散圖。
- 您必須在介面上正確填寫 `Spine Pro CLI 執行檔路徑`。
- 如果 Spine unpack 失敗，請先確認 `matched` 資料夾內確實有：
  - `<heroID>.atlas`
  - `<heroID>.png`
- 您可以選擇「拆圖選取 Hero」只針對已解包的 Hero 拆圖，或是選擇「解包 + 拆圖選取 Hero」一次自動完成。
- 拆出來的圖片會放在對應 Hero 的 `unpacked` 目錄。

### 7. 全自動 Spine GIF 匯出 (推薦)
DawnAssetHelper 支援解包完成後全自動一條龍產出 GIF！
但為了確保相容性，Spine CLI 的 GIF 匯出設定必須由您的 Spine 版本（例如 3.8.75）產生。**請勿依賴自動生成的猜測檔**。

**一次性前置設定 (必做)：**
1. 打開 Spine Pro (您的版本，例如 3.8.75)。
2. 開啟任意可匯出的 Spine project。
3. 點擊 Export (匯出)，選擇 GIF，設定好 FPS / 尺寸 / 背景透明等參數。
4. 點擊 `Save` 儲存為 export settings JSON。
5. 回到 DawnAssetHelper，點擊「**匯入 GIF Preset**」按鈕，選擇您剛剛儲存的 JSON。
6. 程式會將其儲存至內部 (`config/export_presets/default_gif.export.json`)。
7. 介面上會顯示 `GIF Preset Status: Ready`，之後所有的 AutoGIF 都會全自動使用這份 Preset，不用再重新設定！

**操作流程：**
1. 設定 AssetBundle 資料夾、AssetStudio CLI 路徑與 Spine.com 路徑。
2. 確保 GIF Preset Status 為 `Ready`。
3. 掃描並勾選要處理的 Hero。
4. 點擊介面上的「**解包 + 拆圖 + 自動轉 GIF + 開啟結果資料夾**」。
5. 程式會依序：解包 -> 整理三件套 -> 拆小圖 -> 匯入 Spine 專案 -> 匯出 GIF -> 開啟 GIF 資料夾。

您也可以單獨使用「**測試 GIF Preset (不解包)**」按鈕，它會直接讀取已產生的 `.spine` 專案進行轉檔測試，免去重跑解包的等待時間。

### 8. 手動 Spine GIF 匯出 (備用流程)
若您想自己控制 Spine 的匯出細節（例如特定動作或背景）：

1. 點擊介面上的「解包 + 拆圖 + 開啟結果資料夾」。
2. 程式解包後，會在對應的 Hero 目錄建立 `spine_ready` 資料夾：
```text
heroes/<hero_id>/spine_ready/
  <hero_id>.skel
  <hero_id>.atlas
  <hero_id>.png
```
3. 手動開啟 Spine Pro，將資料夾內的檔案匯入，自行調整並匯出 GIF。

### 9. 自訂 CLI Spine GIF 匯出 JSON (進階選填)
如果您不想覆寫內部預設檔，您也可以在「自訂 Spine GIF 匯出 JSON（進階選填）」直接指定外部的 `.export.json` 檔案。指定後，程式便會優先套用該檔案。

### 10. AssetStudio CLI Mode 說明
如果 AssetStudio 匯出失敗，請檢查 GUI 介面上的 **AssetStudio CLI Mode**。

**NewCLI 使用格式**：
```bash
AssetStudio.CLI <input_path> <output_path> --game Normal --types Texture2D,Sprite,TextAsset,MonoBehaviour,AnimationClip --group_assets ByType --export_type Convert
```

**LegacyModCLI 使用格式**：
```bash
AssetStudioModCLI <input_path> -o <output_path> -t tex2d,sprite,textasset,monobehaviour,animationclip --log-output both
```

### 11. 常見錯誤排查
- **AssetStudio 找不到路徑**：請確保點選了正確的 exe/dll 檔案，且路徑沒有特殊無法存取的字元。
- **如果 log 出現 Nothing exported**：
  1. 代表 AssetStudio 成功讀到 AB，但目前 export mode 沒有匯出內容。
  2. DawnAssetHelper 會自動嘗試 Convert / Raw / Dump / JSON。
  3. 如果仍然失敗，請用 AssetStudioGUI 手動確認該 AB 是否真的有 TextAsset / Texture2D / Sprite。
  4. 如果 GUI 看得到但 CLI 匯不出，可能是 CLI 版本與 GUI 行為不同，需要換 CLI 或指定不同 CLI profile。
- **匹配失敗 (Missing skeleton / atlas / texture)**：代表解包出來的內容中沒有對應檔案。請檢查原始 AB 檔是否完整。
- **Spine 匯出警告 (WARNING: Mesh image file dimensions changed)**：
  1. Spine 3.8.75 匯出時可能會出現此警告。
  2. 這通常表示 unpacked 圖片尺寸與 mesh 記錄尺寸不同。
  3. 如果 GIF 輸出結果正常（人物貼圖正確），這可以忽略，DawnAssetHelper 會自動標記為 `success_with_warnings`。
- **程式卡住或沒有回應**：請查看即時 Log 視窗是否有錯誤訊息，或打開對應的 `run.log` 檔案查看詳細過程。若有設定逾時時間，則超時後會自動停止。
- **open.bat 啟動錯誤**：如果 `open.bat` 出現奇怪的 command not recognized，請執行以下指令修復編碼與換行：
  ```bash
  powershell -ExecutionPolicy Bypass -File .\fix_bat_encoding.ps1
  ```
