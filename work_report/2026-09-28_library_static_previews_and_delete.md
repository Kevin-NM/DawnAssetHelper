# 2026-09-28：Library 刪除與靜態預覽

## 使用者需求與起始狀態

使用者已取得 hero1125 圖片，要求停止圖片輸出，改為提供刪除 Library 英雄的方式，
並讓預覽在點進去前不要播放，減少卡頓。
開始前讀取 `2026-09-28_complete_spine_export.md`，工作樹乾淨，仍沒有 CodeGraph index。
hero1125 的輸出在 `output/transparent/hero1125/hero1125_transparent.png`，不再繼續圖片工作。

## 修改與設計

- `src/services/library_service.py`：新增獨立服務。
  - 用 Pillow 從 GIF 取一格（優先第 11 格，短動畫用第 1 格），產生最長邊 256px 的
    RGBA 靜態 PNG，存至 `output/temp/library_thumbnails/`。
  - 依來源絕對路徑、mtime_ns、檔案大小決定快取鍵，重匯出會換縮圖；使用執行緒鎖
    限制同時解碼數，暫存 PNG 寫完才原子替換。
  - 僅允許輸出目錄內 Library/Runs 的 GIF，拒絕 traversal 與連結/Windows junction。
  - 刪除採可復原搬移：同英雄 `heroes/` 與所有 `runs/*/heroes/` 副本都移入
    `trash/<時間>_<hero>_<隨機值>/`，保留相對目錄與 `manifest.json`。
    原始 AssetBundle、其他英雄與英雄自訂名稱/類型 metadata 不變。
    搬移失敗時回復已完成的搬移，避免主項目移走後舊 run 又冒出。
- `server/app.py`：
  - 新增 GET `/api/library-thumbnail?source=<output-relative GIF path>`，回傳小型 PNG。
    由同步路由執行 Pillow，不阻塞 async event loop；列表僅提供 URL，不解碼圖片。
  - Library entry 新增 `static_thumbnail`，GIF detail entry 新增 `thumbnail`，保留既有
    `thumbnail` 動畫 URL 相容原本的開資料夾等流程。
  - 新增 DELETE `/api/library/{hero_id}`。執行中回 409，非法 ID 回 400，缺檔回 404，
    檔案占用/權限問題回 409。刪除同步完成且沒有 await，與此 server instance 的啟動
    工作路由不會在檢查後插入另一個 async 請求。
- `server/templates/index.html`：
  - Library 卡片只載入靜態 PNG，啟用 lazy loading 與 async decoding。
  - 列表與詳情都加入刪除按鈕及確認；卡片按鈕 stopPropagation，不打開詳情。
  - GIF 詳情也使用靜態圖。移除原本用完整 GIF 畫 canvas 與隱藏 GIF 的預載/hover 播放。
    點選單张才建立 animated img，再點停止；最多一張播放，切换时卸載上一張。
  - 關閉清空詳情 DOM 與 GIF src；快速切換/關閉時以 request generation 防止舊回應
    重新填入隱藏的詳情。GIF 載入失敗回到靜態圖。
- `tests/test_library_service.py`、`tests/test_library_api.py`：回歸測試。
- `README.md`：加入使用方式。

## 驗證與狀態

- `./venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'`：
  14 項中 13 項通過，1 項因 Windows 沒有建立 symlink 的權限跳過。
  包含縮圖格式/尺寸/快取/更新、非法路徑、同英雄三份副本搬移、其他英雄保留、搬移
  失敗回復、API 靜態縮圖、刪除後重掃不復現、執行中拒刪與錯誤狀態碼。
- 將 HTML 的 inline scripts 擷取至 `output/temp/library-ui-check.js`，`node --check` 通過。
- 臨時啟動 uvicorn 於 127.0.0.1:8001，使用 in-app browser 驗證實際 UI：
  - 初次列表 11 張圖片全是靜態縮圖 endpoint，natural dimensions 均 <=256，動畫元素 0。
  - hero1201 詳情包含 7 張 variant GIF，未播放前全部為 PNG。
  - 點播放建立 1 個 animated img；切換另一張仍只有 1 個，來源正確切換。
  - 關閉詳情後 animated img 數量與 detail-body 子元素都為 0。
  - 刪除鈕已顯示；嘗試打開 native confirm 時瀏覽器工具因焦點阻塞而 timeout，關閉
    臨時分頁後解除。沒有接受確認、沒有刪除任何使用者的英雄。刪除的實際搬移與 API
    整合已用 TemporaryDirectory 資料完整驗證。
- AI Router 只接收一般測試情境，未外送專案程式碼，返回測試建議已採納於驗證。
- 快取在驗證時 17 張 PNG 合計約 757 KB，避免列表/詳情預載數十 MB 的 GIF。

## 已知限制、未完成項目與接手建議

需求已完成；需重啟原本 DawnAssetHelper server 並重新整理頁面以載入後端與前端。
此次未重啟使用者原本的 server。臨時驗證的 browser tabs 與 8001 server 在結束前關閉。

- 第一次縮圖仍需在本機解碼 GIF 的前幾格；之後走 PNG 快取。損壞 GIF 會顯示卡片
  placeholder，不回退到完整動畫。
- 回收區目前沒有 UI 復原/清空按鈕；可以依批次 `manifest.json` 的 original_paths，
  將該批次中相同相對路徑搬回輸出根目錄。復原前必須確認沒有同名新資料及執行中工作。
- 舊縮圖快取會留在 temp 下；沒有為每次刪除掃描/清理全部快取，避免額外 I/O。
- 路徑連結測試在目前環境未實際建立成功，需有 symlink 權限的環境再跑該測試。
- 現有 GIF 計數/跨 runs fallback、硬編碼 /output URL、其他舊清理/刪 run endpoints
  的行為不在本次修改範圍。其他路由的全面路徑安全檢查可另作獨立工作。
