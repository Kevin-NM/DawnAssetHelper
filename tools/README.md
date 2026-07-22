# tools/

將外部工具放入對應資料夾，DawnAssetHelper 會自動偵測。

## 資料夾結構

```
tools/
├── AssetStudio/          ← AssetStudio CLI
│   └── AssetStudio.CLI.exe
│
├── Spine/                ← Spine Pro CLI (需要授權)
│   └── Spine.com
│
└── spine-exporter/       ← spine-export-cli (npm 全域安裝即可)
    └── (由 npm 管理，通常不需要手動放)
```

## 下載連結

| 工具 | 用途 | 下載 |
|---|---|---|
| AssetStudio | 解包 Unity AssetBundle | https://github.com/Perfare/AssetStudio |
| Spine Pro | Spine 骨骼動畫編輯器 | https://esotericsoftware.com/ |
| spine-exporter | 開源 Spine GIF 匯出 | `npm install -g spine-exporter` |

## 備註

- AssetStudio CLI 放入 `tools/AssetStudio/` 後，設定頁會自動偵測路徑
- Spine Pro 需要有效授權才能使用 CLI 匯出 GIF
- spine-exporter 建議用 npm 全域安裝，設定頁會自動偵測
