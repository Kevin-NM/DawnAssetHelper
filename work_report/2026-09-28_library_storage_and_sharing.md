# 2026-09-28：獨立 Library、清理執行資料與圖片分享

## 需求、起始狀態

使用者要求 Library 與可清理的 output 分開；清理不能誤刪已保存英雄。
詳情 GIF 恢復移上播放，點圖片開原檔；加入圖片複製，以及第三張透明 PNG 主圖。
開始前讀取 `2026-09-28_library_static_previews_and_delete.md`，Git 乾淨，無 `.codegraph/`。
此前 hero1125 已取得，圖片輸出工作維持暫停；本次沒有重新匯出動畫。

## 修改與設計

- `src/app_config.py`：新增 `library_root="./library"`，必須與 output 根目錄互不包含。
  `.gitignore` 忽略 library；web/desktop 保存設定皆保留新欄位。
- `src/services/library_service.py`：
  - 閒置時把舊 output/heroes 的英雄搬至 library/heroes；同名衝突不覆寫、舊資料仍保留。
  - 舊已保存項目可能只有 matched，過去借用 runs 的 GIF。現在先複製最新可用 GIF
    到永久 Library，再允許清理 runs。複製使用 staging，成功才換入 gif，失敗可重試。
  - 全尺寸 PNG 與 256px 縮圖共用取格邏輯（優先第 11 格，短動畫第 1 格）。全圖保留
    RGBA/完整畫布、不裁切、不縮放；來源在 Library 時快取也在 Library，清理 output 不影響。
  - 清理執行資料只永久刪除 output/runs、output/temp，事前驗證整棵樹路徑與連結。
    保護 Library、舊 heroes 同名衝突、resource_index、trash、其他 output 成果。
  - 既有 raw_export/diagnostics 清理也增加完整路徑驗證。
  - 單英雄刪除涵蓋獨立 Library、舊 heroes、所有 runs，同樣搬入 output/trash，可復原。
    manifest 增加兩個根目錄，library/ 開頭項目屬於 library_root，其餘屬 output_root。
- `server/app.py`：
  - Listing/detail 支援新永久根目錄与舊衝突根目錄，Library 優先。新增 /library/{source}
    圖片路由及 /api/library-image 全尺寸 PNG。縮圖 endpoint 新增 area=library/output。
  - detail 提供 folder_path、main_image、GIF 的 image PNG URL。
  - 保留最新 matched-only run 借用前次 GIF 的 fallback，修正所有 variant GIF 的計數。
  - promote 完整複製到 Library staging，成功後才發布，取代舊版本時保留 library/versions。
    磁碟不足/複製失敗不破壞既有保存項目。
  - 新增 POST /api/clean-generated；清理、保存、刪除都拒絕工作執行中的請求。
- `server/templates/index.html`：
  - 列表靜態不變；GIF 圖片 mouseenter/focus 才載入，leave/blur 卸載，始終最多一張。
    關閉詳情或視窗失焦同樣卸載；點圖片用新分頁開完整 GIF。
  - 第三張透明主圖有開啟、複製、下載；各 GIF 有「複製靜態圖」。
  - ClipboardItem 以 image/png promise 在使用者點擊時立即呼叫 clipboard.write，等待成功
    才顯示成功。保留原始 Blob，未經 canvas 或 thumbnail；不支援/權限拒絕/缺檔會明確報錯。
  - 新增清理執行資料按鈕與永久刪除範圍確認。開資料夾使用 backend 路徑。
  - Library 卡片增加鍵盤 Enter/Space 操作。
- `src/services/orchestrator.py`、`src/ui/main_window.py`：GIF/資源重用、桌面查檔/開資料夾
  增加永久 Library 查找，清掉 runs 後仍可使用已保存 matched/GIF。
- `README.md`、Python/Node 測試、此報告。

## 驗證

- Python unittest：22 項，21 通過、1 因 Windows symlink 權限跳過。
  涵蓋搬移冪等、同名不覆寫、借用 GIF 永久保存、清理保留 Library/索引、根目錄重疊拒絕、
  全尺寸 alpha/頂底像素、圖片快取、失敗複製不能發布半成品/不能清掉完整 run、promote
  失敗保留舊 Library、三類刪除來源、清理/刪除執行中拒絕、非法 area/路徑。
- `node tests/library-media.test.mjs`：5 項通過，實際抽取 HTML 中 UI 函式，測 hover/focus
  的單張載入/卸載、GIF 失敗回靜態、clipboard 在 fetch 完成前即啟動且等成功、unsupported、
  denied、missing PNG 無假成功。`node --test` 在 sandbox spawn EPERM，直接 Node 執行
  node:test 可完整測試，不需修改 assertion。
- HTML inline JS 擷取到 output/temp/library-ui-check.js，`node --check` 通過。
- 臨時 uvicorn 127.0.0.1:8001，in-app browser 實際驗證：
  - 9 個舊保存英雄搬至 `D:/Antigravity/DawnAssetHelper/library/heroes`，output/heroes 為空。
  - hero11221 顯示 idle、wait、第三張透明 PNG，列表/詳情初始動畫 0。
  - GIF 鍵盤 focus 載入 1 張，blur 後 0，對應真實事件處理程式。
  - 複製主圖等待 clipboard.write 成功，出現「已複製，可直接貼上」。
  - 點主圖開新分頁，標題 `library-image (1402×1538)`；Pillow 驗證 alpha 範圍 0..255，
    PNG 1,052,609 bytes。未送出任何社群貼文；未跨社群平台驗證貼上。
  - 截圖 `output/library-sharing-preview.png`。
  - 未在使用者資料上按下清理或刪除確認，破壞性流程只對 TemporaryDirectory 測試資料執行。
- AI Router 接收一般化的搬移/清理/Clipboard 測試需求，返回 edge cases；未外送原圖或程式碼。

## 當前狀態、限制、接手建議

需求完成。臨時測試分頁及 8001 server 結束前關閉；需使用者重啟日常 server 並重整頁面。
Library 的實際搬移已完成，即使再次啟動會冪等跳過。未清理使用者 runs/temp/trash。

- PNG 是原 GIF 的代表影格，保留來源已有透明度與陰影；無法補回來源本身已被裁掉的畫素。
  之前完整 GIF 匯出修正仍存在，未變更。
- clipboard 複製的是 PNG 靜態圖。不同社群接收圖片/透明背景能力不同，失敗可下載 PNG。
  Browser session 的工具 clipboard.read 未返回 OS 寫入的項目；以網站 clipboard.write
  fulfilled UI 和 Blob 單測驗證，不聲稱所有平台貼上均已測過。
- 同名 migration 衝突保留舊 output/heroes，API 優先顯示新 Library；必要時人工比對合併。
  copy staging、library/versions 和圖像 cache 不由 output 清理按鈕清除。
- cleanup 遇到檔案占用可能部分已刪、部分留存，回 409；已保存 Library 保護不受影響。
- 目前 symlink 真實測試仍受本機權限限制；可在有權限的環境補跑。
- output 靜態 mount 和一些舊診斷工具的 output/heroes 硬編碼是既有問題，不在本次全面改造。
  如另改自訂 output_root 的 static serving 或維護用 force-trim，請額外檢查它們。
- 回收復原/清空、版本與 Library cache 管理尚無 UI，屬後續功能，不影響本次需求。
