# 2026-09-28：修正 Spine GIF 上下截斷

## 需求與起始狀態

使用者提供婚紗角色及操偶師的 GIF 截圖，希望輸出完整角色，避免自動裁切掉頭頂、腳底與動畫伸出的內容。
開始時工作樹乾淨，沒有 `.codegraph/` 或既有 `work_report/`。

## 根因

- 實際使用的是 NVM 管理的 npm `spine-exporter` 0.8.0。其 renderer 將
  `canvas.height` 設為 `viewport.width`，使直立角色被放進正方形画布而截掉上下。
- 專案舊 PowerShell 補丁另外把影格依面積排序後排除最大的 10%，會犧牲真正的
  極端動作；並強制啟用 `sharp.trim()`。實際這次使用的 npm 安裝沒有套用該舊補丁，
  但修正也會覆蓋已套用舊補丁的安裝。
- 原本範圍採樣不包含最終姿勢，也沒有與 renderer 的低 FPS delta cap 對齊。

## 修改內容與設計

- `patches/spine-exporter-loader.mjs`：透過 Node ESM loader 在記憶體修正 renderer
  與 handler，不修改使用者的全域 npm 安裝。
  使用 AnimationState 採樣所有實際播放姿勢、起始和最終姿勢；與 TimeKeeper 的
  `min(1 / fps, 0.064)` 對齊。保留所有範圍，四邊加 2 個輸出像素，尺寸向上取整。
  高度使用正確的 viewport.height。停用 autoCrop。
- `src/services/spine_exporter_service.py`：解析相鄰 npm 套件或以 `npm root -g`
  解析 NVM shim；用 Node 加載上述 loader 執行 CLI。找不到 npm 套件或 Node 時清楚失敗。
  CLI 的 positional input 放在 selected-animation array 之前，避免 yargs 吞掉輸入路徑。
  不支援的 renderer/handler 版型會中止；即使目的資料夾有舊 GIF，也不誤報成功。
- `patches/apply-spine-exporter-patches.ps1`：保留選用的手動補丁入口，改用同一份 JS
  轉換邏輯，支援 `-PackageRoot`，以 npm root 尋找安裝並保存首次 `.dawn-backup`。
- `patches/README.md`、`README.md`：說明完整匯出行為、重啟、重匯出与相容性。
- `tests/spine-exporter-loader.test.mjs`、`tests/test_spine_exporter_service.py`：新增回歸測試。

## 驗證

- `node tests/spine-exporter-loader.test.mjs`：6 個測試通過。
- `./venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'`：5 個測試通過。
- 實際設定的 NVM CLI validation 通過。
- 以既有 skeleton/atlas/PNG 重新匯出兩個 `idle`，20 FPS、scale 0.5，兩次 exit code 0。
  保留原始 GIF，結果另存 `output/crop_validation/`（Git ignored）。

| 角色 | 舊尺寸 | 新尺寸 | 影格數 | 舊觸邊影格 | 新觸邊影格 |
| --- | --- | --- | --- | --- | --- |
| hero11221 婚紗 | 1398×1398 | 1402×1538 | 147 → 147 | 147 | 0 |
| hero1124 操偶師 | 1852×1852 | 1856×2422 | 147 → 147 | 147 | 0 |

逐格檢查 RGBA alpha bbox，確認新結果所有可見像素都在畫布內。
婚紗所有影格合併 bbox 為 `(115, 6, 1398, 1490)`；操偶師為 `(68, 5, 1828, 2410)`。
檢視兩張 `preview.png`，確認完整頭飾、裙襬、手臂、背部裝置和腳部。
檢查資料留在 `output/crop_validation/verification.json`。

Node `--test` 的子程序以及沙箱內 ffmpeg spawn 出現 EPERM；改以直接執行 test.mjs
完成 Node 測試，實際 GIF 匯出透過已核准的本機沙箱提升完成。不是程式回歸。
AI Router 的分析呼叫被自動審核拒絕（外送授權不足）；後续分析與驗證全部在本機完成。

## 目前狀態、限制與後續

需求已完成，沒有待完成的實作。需要重啟現有 DawnAssetHelper 進程，之後正常的
spine-exporter 匯出就會自动使用修正。這次沒有重啟使用者原本執行中的軟體。

- 舊 GIF 中已截掉的像素無法直接復原，需從 matched 素材重匯出。
- 保留原本 FPS、scale、PMA 與後處理設定；沒有強制改成 scale 1 或修改 Spine Pro 引擎。
- 完整範圍可能讓輸出更大。這次測試直接 CLI GIF 約 73 MB / 179 MB；沒有套用 pipeline
  既有 ffmpeg 後處理。檔案壓縮、淡入移除與調色盤行為不在本次修改範圍。
- 目前支援 0.8.0 的來源版型及舊專案補丁；npm 大版更新若改變來源，需要更新匹配器。
- Node 18.19.1 的 experimental-loader 會印出棄用方向警告，實際輸出正常。
- 接手時先看此紀錄與 `patches/README.md`；若升級匯出器，重跑上述測試並以兩角色確認。
