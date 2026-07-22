import os
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFileDialog, QGroupBox, QTableWidget, 
    QTableWidgetItem, QHeaderView, QTextEdit, QProgressBar, QMessageBox,
    QAbstractItemView, QCheckBox, QComboBox, QSpinBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor

from src.app_config import AppConfig
from src.utils.json_utils import load_json, save_json
from src.services.logger_service import logger
from src.services.hero_scanner import HeroScanner
from src.services.orchestrator import OrchestratorWorker
from src.services.assetstudio_service import AssetStudioService
from src.services.spine_service import SpineService

CONFIG_PATH = Path("config/appsettings.json")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DawnAssetHelper")
        self.resize(1000, 800)
        
        self.config = AppConfig()
        self.load_config()
        
        self.heroes = []
        self.worker = None
        
        self.init_ui()
        logger.log_signal.connect(self.append_log)
        
        logger.info("Application started.")

    def load_config(self):
        if CONFIG_PATH.exists():
            data = load_json(CONFIG_PATH, {})
            for k, v in data.items():
                if hasattr(self.config, k):
                    setattr(self.config, k, v)
        else:
            self.save_config()
            
    def save_config(self):
        data = {
            "asset_bundle_folder": self.config.asset_bundle_folder,
            "assetstudio_path": self.config.assetstudio_path,
            "spine_cli_path": self.config.spine_cli_path,
            "spine_exporter_path": self.config.spine_exporter_path,
            "gif_engine": self.config.gif_engine,
            "spine_gif_export_settings_path": self.config.spine_gif_export_settings_path,
            "use_internal_default_gif_preset": self.config.use_internal_default_gif_preset,
            "internal_default_gif_preset_path": self.config.internal_default_gif_preset_path,
            "gif_postprocess_enabled": self.config.gif_postprocess_enabled,
            "gif_auto_trim_bad_leading_frames": self.config.gif_auto_trim_bad_leading_frames,
            "gif_trim_max_scan_frames": self.config.gif_trim_max_scan_frames,
            "output_root": self.config.output_root,
            "timeout_minutes": self.config.timeout_minutes,
            "assetstudio_export_types": self.config.assetstudio_export_types,
            "assetstudio_cli_profile": self.config.assetstudio_cli_profile,
            "assetstudio_game": self.config.assetstudio_game,
            "assetstudio_types": self.config.assetstudio_types,
            "assetstudio_group_assets": self.config.assetstudio_group_assets,
            "assetstudio_export_type": self.config.assetstudio_export_type,
            "assetstudio_fallback_export_types": self.config.assetstudio_fallback_export_types,
            "assetstudio_try_without_types": self.config.assetstudio_try_without_types,
            "assetstudio_use_absolute_output_path": self.config.assetstudio_use_absolute_output_path,
            "assetstudio_export_whole_hero_group_folder": self.config.assetstudio_export_whole_hero_group_folder,
            "gif_trim_fallback_by_animation": self.config.gif_trim_fallback_by_animation
        }
        save_json(CONFIG_PATH, data)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 1. Settings Area
        settings_group = QGroupBox("設定區")
        settings_layout = QVBoxLayout()
        
        self.input_ab_folder = self._create_path_row(settings_layout, "遊戲資源目錄:", self.config.asset_bundle_folder, True)
        self.input_as_path = self._create_path_row(settings_layout, "AssetStudio CLI 路徑:", self.config.assetstudio_path, False)
        
        cli_mode_row = QHBoxLayout()
        cli_mode_row.addWidget(QLabel("AssetStudio CLI Mode:"))
        self.cb_cli_profile = QComboBox()
        self.cb_cli_profile.addItems(["Auto", "NewCLI", "LegacyModCLI"])
        if self.config.assetstudio_cli_profile in ["Auto", "NewCLI", "LegacyModCLI"]:
            self.cb_cli_profile.setCurrentText(self.config.assetstudio_cli_profile)
        cli_mode_row.addWidget(self.cb_cli_profile)
        settings_layout.addLayout(cli_mode_row)
        
        self.input_spine_path = self._create_path_row(settings_layout, "Spine.com 路徑:", self.config.spine_cli_path, False)
        self.input_spine_exporter_path = self._create_path_row(settings_layout, "spine-export-cli 路徑:", self.config.spine_exporter_path, False)

        gif_engine_row = QHBoxLayout()
        gif_engine_row.addWidget(QLabel("GIF 匯出引擎:"))
        self.cb_gif_engine = QComboBox()
        self.cb_gif_engine.addItems(["spine_cli", "spine_exporter"])
        if self.config.gif_engine in ["spine_cli", "spine_exporter"]:
            self.cb_gif_engine.setCurrentText(self.config.gif_engine)
        gif_engine_row.addWidget(self.cb_gif_engine)
        gif_engine_row.addStretch()
        settings_layout.addLayout(gif_engine_row)

        self.input_spine_settings = self._create_path_row(settings_layout, "自訂 Spine GIF 匯出 JSON（進階選填）:", self.config.spine_gif_export_settings_path, False)
        
        btn_import_preset = QPushButton("匯入 GIF Preset")
        btn_import_preset.clicked.connect(self.on_import_gif_preset)
        
        self.lbl_active_preset = QLabel(f"Active GIF preset: {self.config.internal_default_gif_preset_path if self.config.use_internal_default_gif_preset else self.config.spine_gif_export_settings_path}")
        self.lbl_active_preset.setStyleSheet("color: #005f00; font-weight: bold; font-size: 11px;")
        
        self.lbl_preset_status = QLabel("GIF Preset Status: Checking...")
        self.lbl_preset_status.setStyleSheet("font-weight: bold; font-size: 11px;")
        
        hint_layout = QHBoxLayout()
        hint_layout.addWidget(btn_import_preset)
        
        text_layout = QVBoxLayout()
        text_layout.addWidget(self.lbl_active_preset)
        text_layout.addWidget(self.lbl_preset_status)
        text_layout.setSpacing(2)
        
        hint_layout.addLayout(text_layout)
        hint_layout.addStretch()
        settings_layout.addLayout(hint_layout)
        
        self._update_preset_status()
        self.input_output_root = self._create_path_row(settings_layout, "輸出根目錄:", self.config.output_root, True)
        
        gif_settings_layout = QHBoxLayout()
        self.sb_idle_trim = QSpinBox()
        self.sb_idle_trim.setRange(0, 10)
        self.sb_idle_trim.setValue(self.config.gif_trim_fallback_by_animation.get("idle", 2))
        self.sb_idle_trim.valueChanged.connect(self._update_force_trim_button_text)
        
        gif_settings_layout.addWidget(QLabel("Idle GIF 前導幀檢查數:"))
        gif_settings_layout.addWidget(self.sb_idle_trim)
        gif_settings_layout.addStretch()
        settings_layout.addLayout(gif_settings_layout)
        
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("儲存設定")
        btn_save.clicked.connect(lambda: self.on_save_config(show_message=True))
        btn_test_as = QPushButton("測試 AssetStudio")
        btn_test_as.clicked.connect(self.on_test_assetstudio)
        btn_test_spine = QPushButton("測試 Spine CLI")
        btn_test_spine.clicked.connect(self.on_test_spine)
        btn_test_exporter = QPushButton("測試 spine-exporter")
        btn_test_exporter.clicked.connect(self.on_test_spine_exporter)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_test_as)
        btn_layout.addWidget(btn_test_spine)
        btn_layout.addWidget(btn_test_exporter)
        btn_layout.addStretch()
        settings_layout.addLayout(btn_layout)
        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)
        
        # 2. Hero List Area
        hero_group = QGroupBox("Hero 清單區")
        hero_layout = QVBoxLayout()
        
        filter_btn_layout1 = QHBoxLayout()
        btn_scan = QPushButton("掃描 Hero")
        btn_scan.clicked.connect(self.on_scan_heroes)
        btn_select_all = QPushButton("全選")
        btn_select_all.clicked.connect(lambda: self.set_check_state(True))
        btn_clear_sel = QPushButton("清除選取")
        btn_clear_sel.clicked.connect(lambda: self.set_check_state(False))
        btn_sel_orig = QPushButton("全選 Original")
        btn_sel_orig.clicked.connect(lambda: self.set_check_state_by_type("Original", True))
        btn_sel_collab = QPushButton("全選 Collab")
        btn_sel_collab.clicked.connect(lambda: self.set_check_state_by_type("Collab", True))
        btn_sel_skin = QPushButton("全選 Skin")
        btn_sel_skin.clicked.connect(lambda: self.set_check_state_by_type("Skin", True))
        
        filter_btn_layout1.addWidget(btn_scan)
        filter_btn_layout1.addWidget(btn_select_all)
        filter_btn_layout1.addWidget(btn_clear_sel)
        filter_btn_layout1.addWidget(btn_sel_orig)
        filter_btn_layout1.addWidget(btn_sel_collab)
        filter_btn_layout1.addWidget(btn_sel_skin)
        filter_btn_layout1.addStretch()
        hero_layout.addLayout(filter_btn_layout1)

        filter_btn_layout2 = QHBoxLayout()
        btn_show_all = QPushButton("只顯示全部")
        btn_show_all.clicked.connect(lambda: self.filter_table(None))
        btn_show_orig = QPushButton("只顯示 Original")
        btn_show_orig.clicked.connect(lambda: self.filter_table("Original"))
        btn_show_collab = QPushButton("只顯示 Collab")
        btn_show_collab.clicked.connect(lambda: self.filter_table("Collab"))
        btn_show_skin = QPushButton("只顯示 Skin")
        btn_show_skin.clicked.connect(lambda: self.filter_table("Skin"))
        btn_show_partial = QPushButton("只顯示 Partial")
        btn_show_partial.clicked.connect(lambda: self.filter_table_by_status("Partial"))

        filter_btn_layout2.addWidget(btn_show_all)
        filter_btn_layout2.addWidget(btn_show_orig)
        filter_btn_layout2.addWidget(btn_show_collab)
        filter_btn_layout2.addWidget(btn_show_skin)
        filter_btn_layout2.addWidget(btn_show_partial)
        filter_btn_layout2.addStretch()
        hero_layout.addLayout(filter_btn_layout2)
        
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["勾選", "Hero ID", "類型", "Variant", "AB 數量", "總大小(KB)", "狀態"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hero_layout.addWidget(self.table)
        hero_group.setLayout(hero_layout)
        main_layout.addWidget(hero_group)
        
        # 3. Execution & Log Area
        exec_group = QGroupBox("執行與 Log 區")
        exec_layout = QVBoxLayout()
        
        exec_btn_layout1 = QHBoxLayout()
        exec_btn_layout2 = QHBoxLayout()
        
        self.btn_run_extract = QPushButton("1. 解包")
        self.btn_run_extract.clicked.connect(lambda: self.on_run("extract_only"))
        
        self.btn_run_unpack = QPushButton("2. 拆圖")
        self.btn_run_unpack.clicked.connect(lambda: self.on_run("unpack_only"))
        
        self.btn_run_both = QPushButton("解包 + 拆圖")
        self.btn_run_both.clicked.connect(lambda: self.on_run("extract_and_unpack"))
        
        self.btn_run_gif = QPushButton("3. 自動轉 GIF（使用現有素材）")
        self.btn_run_gif.clicked.connect(lambda: self.on_run("gif_only"))
        self.btn_run_gif.setStyleSheet("background-color: #fca311; color: #333; border: 1px solid #ccc; padding: 4px; font-weight: bold;")
        
        self.btn_run_extract_gif = QPushButton("解包 + 自動轉 GIF")
        self.btn_run_extract_gif.clicked.connect(lambda: self.on_run("extract_and_gif"))
        
        self.btn_run_full = QPushButton("一鍵：解包 + 拆圖 + GIF")
        self.btn_run_full.clicked.connect(lambda: self.on_run("full"))
        
        self.btn_run_full_open = QPushButton("一鍵完成並開啟")
        self.btn_run_full_open.clicked.connect(lambda: self.on_run("full_open"))
        
        self.btn_test_gif = QPushButton("測試 GIF Preset")
        self.btn_test_gif.clicked.connect(lambda: self.on_run("test_gif_preset"))
        self.btn_test_gif.setVisible(False)
        
        self.btn_run_extract_unpack_open = QPushButton("解包 + 拆圖 + 開啟結果資料夾")
        self.btn_run_extract_unpack_open.clicked.connect(lambda: self.on_run("extract_unpack_and_open"))
        self.btn_run_extract_unpack_open.setVisible(False)
        
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_stop.setEnabled(False)
        
        btn_open_out = QPushButton("開啟輸出資料夾")
        btn_open_out.clicked.connect(self.on_open_output)
        
        btn_open_hero = QPushButton("開啟選取 Hero 結果")
        btn_open_hero.clicked.connect(self.on_open_hero_folder)
        
        btn_clear_log = QPushButton("清除 Log")
        btn_clear_log.clicked.connect(self.on_clear_log)
        
        exec_btn_layout1.addWidget(self.btn_run_extract)
        exec_btn_layout1.addWidget(self.btn_run_unpack)
        exec_btn_layout1.addWidget(self.btn_run_both)
        exec_btn_layout1.addWidget(self.btn_run_gif)
        exec_btn_layout1.addWidget(self.btn_run_full)
        exec_btn_layout1.addWidget(self.btn_run_full_open)
        
        self.btn_retest_gif = QPushButton("重新轉 GIF")
        self.btn_retest_gif.clicked.connect(self.on_retest_gif)
        self.btn_retest_gif.setStyleSheet("background-color: #fca311; color: #333; border: 1px solid #ccc; padding: 4px; font-weight: bold;")
        self.btn_retest_gif.setVisible(False)
        
        exec_btn_layout2.addWidget(self.btn_run_extract_gif)
        exec_btn_layout2.addWidget(btn_open_hero)
        exec_btn_layout2.addWidget(self.btn_stop)
        exec_btn_layout2.addWidget(btn_open_out)
        exec_btn_layout2.addWidget(btn_clear_log)
        
        self.btn_open_spine = QPushButton("開啟 .spine 專案")
        self.btn_open_spine.clicked.connect(self.on_open_spine_project)
        self.btn_open_spine.setVisible(False)
        
        self.btn_inspect_spine = QPushButton("檢查 Images Path")
        self.btn_inspect_spine.clicked.connect(self.on_inspect_spine_project)
        self.btn_inspect_spine.setVisible(False)
        
        self.btn_manual_baseline = QPushButton("手動專案 CLI 匯出測試")
        self.btn_manual_baseline.clicked.connect(self.on_manual_baseline_test)
        self.btn_manual_baseline.setVisible(False)
        
        self.btn_compare_projects = QPushButton("比較自動與手動專案")
        self.btn_compare_projects.clicked.connect(self.on_compare_projects)
        self.btn_compare_projects.setVisible(False)
        
        self.btn_gif_postprocess = QPushButton("重新檢查 GIF")
        self.btn_gif_postprocess.clicked.connect(self.on_gif_postprocess)
        self.btn_gif_postprocess.setVisible(False)
        
        self.btn_force_trim_idle = QPushButton(f"檢查 idle 前 {self.sb_idle_trim.value()} 幀")
        self.btn_force_trim_idle.clicked.connect(self.on_force_trim_idle)
        self.btn_force_trim_idle.setVisible(False)
        
        exec_layout.addLayout(exec_btn_layout1)
        exec_layout.addLayout(exec_btn_layout2)
        
        self.lbl_step = QLabel("目前步驟: 尚未執行")
        exec_layout.addWidget(self.lbl_step)
        
        self.progress = QProgressBar()
        self.progress.setValue(0)
        exec_layout.addWidget(self.progress)
        
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        exec_layout.addWidget(self.txt_log)
        
        exec_group.setLayout(exec_layout)
        main_layout.addWidget(exec_group)

    def _create_path_row(self, layout, label_text, default_val, is_dir):
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        line_edit = QLineEdit(default_val)
        row.addWidget(line_edit)
        btn_browse = QPushButton("瀏覽")
        
        def browse():
            if is_dir:
                path = QFileDialog.getExistingDirectory(self, "選取資料夾", line_edit.text() or "")
            else:
                path, _ = QFileDialog.getOpenFileName(self, "選取檔案", line_edit.text() or "", "All Files (*.*)")
            if path:
                path = os.path.normpath(path)
                line_edit.setText(path)
                
        btn_browse.clicked.connect(browse)
        row.addWidget(btn_browse)
        layout.addLayout(row)
        return line_edit

    def _update_force_trim_button_text(self):
        self.btn_force_trim_idle.setText(f"檢查 idle 前 {self.sb_idle_trim.value()} 幀")

    def on_save_config(self, show_message: bool = True):
        self.config.asset_bundle_folder = self.input_ab_folder.text()
        self.config.assetstudio_path = self.input_as_path.text()
        self.config.assetstudio_cli_profile = self.cb_cli_profile.currentText()
        self.config.spine_cli_path = self.input_spine_path.text()
        self.config.spine_exporter_path = self.input_spine_exporter_path.text()
        self.config.gif_engine = self.cb_gif_engine.currentText()
        self.config.spine_gif_export_settings_path = self.input_spine_settings.text()
        self.input_output_root.setText(self.input_output_root.text().strip())
        self.config.output_root = self.input_output_root.text()
        
        # Update idle trim fallback
        if not hasattr(self.config, 'gif_trim_fallback_by_animation'):
            self.config.gif_trim_fallback_by_animation = {}
        self.config.gif_trim_fallback_by_animation["idle"] = self.sb_idle_trim.value()
        
        self.save_config()
        if show_message:
            QMessageBox.information(self, "提示", "設定已儲存！")

    def on_import_gif_preset(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇 Spine GIF Export JSON", "", "JSON Files (*.json)")
        if not file_path:
            return
            
        try:
            import json
            from src.services.export_preset_manager import ExportPresetManager
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            bg = data.get("background")
            needs_warning = bg in ["00000000", "#00000000", "0x00000000", "transparent", ""]
            data = ExportPresetManager.normalize_gif_preset(data)
            
            target_path = Path("config/export_presets/default_gif.export.json")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            self.config.use_internal_default_gif_preset = True
            self.input_spine_settings.clear()
            self.on_save_config()
            self.lbl_active_preset.setText(f"Active GIF preset: {target_path}")
            self._update_preset_status()
            
            msg = f"已成功匯入 Preset 至 {target_path}。\n之後 AutoGIF 將全自動使用此預設檔！"
            if needs_warning:
                msg = f"警告：此 Preset 包含 background={bg}，與 Spine 3.8.75 不相容。已自動為您修正 (Normalized) 為 null。\n\n" + msg
                
            QMessageBox.information(self, "匯入成功", msg)
            logger.info(f"Imported custom GIF preset to internal default: {file_path}")
        except Exception as e:
            QMessageBox.warning(self, "匯入失敗", str(e))
            logger.error(f"Failed to import GIF preset: {e}")

    def _update_preset_status(self):
        preset_path = Path("config/export_presets/default_gif.export.json")
        if preset_path.exists():
            self.lbl_preset_status.setText("GIF Preset Status: Ready")
            self.lbl_preset_status.setStyleSheet("color: green; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_preset_status.setText("GIF Preset Status: Not initialized (Please import a preset)")
            self.lbl_preset_status.setStyleSheet("color: red; font-weight: bold; font-size: 11px;")

    def on_test_assetstudio(self):
        self.config.assetstudio_path = self.input_as_path.text()
        self.config.assetstudio_cli_profile = self.cb_cli_profile.currentText()
        srv = AssetStudioService(self.config.assetstudio_path, self.config)
        success, output = srv.test_cli()
        if success:
            QMessageBox.information(self, "測試結果", output)
        else:
            QMessageBox.warning(self, "測試結果", output)

    def on_test_spine(self):
        path = self.input_spine_path.text()
        srv = SpineService(path)
        if srv.validate():
            QMessageBox.information(self, "測試結果", "Spine CLI 路徑有效！")
        else:
            QMessageBox.warning(self, "測試結果", "Spine CLI 路徑無效或檔案不存在。")

    def on_test_spine_exporter(self):
        path = self.input_spine_exporter_path.text()
        if not path:
            QMessageBox.warning(self, "測試結果", "請先設定 spine-export-cli 路徑！")
            return
        from src.services.spine_exporter_service import SpineExporterService
        srv = SpineExporterService(path)
        if srv.validate():
            QMessageBox.information(self, "測試結果", "spine-export-cli 路徑有效！")
        else:
            QMessageBox.warning(self, "測試結果", "spine-export-cli 路徑無效或檔案不存在。\n請確認已安裝: npm i -g spine-exporter")

    def on_scan_heroes(self):
        ab_folder = self.input_ab_folder.text()
        if not ab_folder or not os.path.exists(ab_folder):
            QMessageBox.warning(self, "錯誤", "請設定正確的遊戲資源目錄！")
            return
            
        logger.info(f"Scanning AssetBundles in {ab_folder} ...")
        scanner = HeroScanner(ab_folder)
        self.heroes = scanner.scan()
        logger.info(f"Scan complete. Found {len(self.heroes)} heroes.")
        self.populate_table()

    def populate_table(self):
        self.table.setRowCount(len(self.heroes))
        for row, hero in enumerate(self.heroes):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk)
            self.table.setItem(row, 1, QTableWidgetItem(hero.hero_id))
            self.table.setItem(row, 2, QTableWidgetItem(hero.hero_type))
            self.table.setItem(row, 3, QTableWidgetItem(", ".join(hero.variant_ids)))
            self.table.setItem(row, 4, QTableWidgetItem(str(hero.file_count)))
            self.table.setItem(row, 5, QTableWidgetItem(str(hero.total_size // 1024)))
            
            status_item = QTableWidgetItem(hero.status)
            if hero.status == "Partial":
                status_item.setForeground(QColor("orange"))
            else:
                status_item.setForeground(QColor("green"))
            self.table.setItem(row, 6, status_item)

    def set_check_state(self, state: bool):
        st = Qt.Checked if state else Qt.Unchecked
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                self.table.item(row, 0).setCheckState(st)

    def set_check_state_by_type(self, hero_type: str, state: bool):
        st = Qt.Checked if state else Qt.Unchecked
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                if self.table.item(row, 2).text() == hero_type:
                    self.table.item(row, 0).setCheckState(st)

    def filter_table(self, hero_type: str | None):
        for row in range(self.table.rowCount()):
            if hero_type is None:
                self.table.setRowHidden(row, False)
            else:
                self.table.setRowHidden(row, self.table.item(row, 2).text() != hero_type)

    def filter_table_by_status(self, status: str):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, self.table.item(row, 6).text() != status)

    def on_retest_gif(self):
        self.on_run(mode="test_gif_preset_open")

    def get_selected_heroes(self):
        selected_heroes = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                hero_id = self.table.item(row, 1).text()
                hero = next((h for h in self.heroes if h.hero_id == hero_id), None)
                if hero:
                    selected_heroes.append(hero)
        return selected_heroes

    def on_run(self, mode: str = "extract_only"):
        if self.worker is not None and self.worker.isRunning():
            return
            
        self.on_save_config(show_message=False) # ensure config is updated
            
        selected_heroes = self.get_selected_heroes()
                    
        if not selected_heroes:
            QMessageBox.information(self, "提示", "請先勾選要處理的 Hero！")
            return
            
        self.on_save_config(show_message=False) # ensure config is updated
        
        self.btn_run_extract.setEnabled(False)
        self.btn_run_unpack.setEnabled(False)
        self.btn_run_both.setEnabled(False)
        self.btn_run_gif.setEnabled(False)
        self.btn_run_extract_gif.setEnabled(False)
        self.btn_run_full.setEnabled(False)
        self.btn_run_full_open.setEnabled(False)
        self.btn_test_gif.setEnabled(False)
        self.btn_retest_gif.setEnabled(False)
        self.btn_run_extract_unpack_open.setEnabled(False)
        self.btn_open_spine.setEnabled(False)
        self.btn_inspect_spine.setEnabled(False)
        self.btn_manual_baseline.setEnabled(False)
        self.btn_compare_projects.setEnabled(False)
        self.btn_gif_postprocess.setEnabled(False)
        self.btn_force_trim_idle.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        
        self.worker = OrchestratorWorker(self.config, selected_heroes, mode)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.step_updated.connect(self.lbl_step.setText)
        self.worker.finished.connect(self.on_run_finished)
        self.worker.start()

    def on_stop(self):
        if self.worker:
            logger.info("Stopping...")
            self.btn_stop.setEnabled(False)
            self.worker.cancel()

    def update_progress(self, current, total):
        self.progress.setMaximum(total)
        self.progress.setValue(current)

    def on_run_finished(self, summary: dict = None):
        self.btn_run_extract.setEnabled(True)
        self.btn_run_unpack.setEnabled(True)
        self.btn_run_both.setEnabled(True)
        self.btn_run_gif.setEnabled(True)
        self.btn_run_extract_gif.setEnabled(True)
        self.btn_run_full.setEnabled(True)
        self.btn_run_full_open.setEnabled(True)
        self.btn_test_gif.setEnabled(True)
        self.btn_retest_gif.setEnabled(True)
        self.btn_run_extract_unpack_open.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_open_spine.setEnabled(True)
        self.btn_inspect_spine.setEnabled(True)
        self.btn_manual_baseline.setEnabled(True)
        self.btn_compare_projects.setEnabled(True)
        self.btn_gif_postprocess.setEnabled(True)
        self.btn_force_trim_idle.setEnabled(True)
        self.lbl_step.setText("目前步驟: 執行完畢")
        
        if summary:
            # Check for specific warning statuses
            if any(h.get("status") == "output_with_mesh_dimension_warnings" for h in summary.get("heroes", [])):
                QMessageBox.warning(self, "提示", "AutoGIF exported files, but visual output may be incorrect due to mesh image dimension warnings.")
            elif any(h.get("status") == "success_with_warnings" for h in summary.get("heroes", [])):
                QMessageBox.warning(self, "提示", "AutoGIF completed with warnings.\nPlease inspect GIF output.")
        
    def on_manual_baseline_test(self):
        project_path, _ = QFileDialog.getOpenFileName(self, "選擇手動成功的 .spine 專案", "", "Spine Project (*.spine)")
        if not project_path:
            return
            
        logger.info(f"[Diagnostic] Starting Manual Baseline Test for: {project_path}")
        from src.services.orchestrator import Orchestrator
        orch = Orchestrator(self.config)
        res = orch.manual_baseline_export_test(project_path)
        
        if res["status"] in ["success", "success_with_warnings", "output_with_mesh_dimension_warnings"]:
            msg = f"匯出完成！\n狀態: {res['status']}\nGIF 數量: {res['gif_count']}\nMesh 警告數: {res['mesh_warning_count']}\n輸出目錄: {res['output_dir']}"
            QMessageBox.information(self, "診斷結果", msg)
        else:
            msg = f"匯出失敗！\n錯誤: {', '.join(res['errors'])}"
            QMessageBox.critical(self, "診斷結果", msg)

    def on_compare_projects(self):
        selected_heroes = self.get_selected_heroes()
        if not selected_heroes:
            QMessageBox.warning(self, "警告", "請先選取一個 Hero 進行比較！")
            return
            
        hero = selected_heroes[0]
        manual_path, _ = QFileDialog.getOpenFileName(self, f"選擇 {hero.hero_id} 手動成功的 .spine 專案", "", "Spine Project (*.spine)")
        if not manual_path:
            return
            
        logger.info(f"[Diagnostic] Comparing Auto vs Manual for {hero.hero_id}")
        from src.services.orchestrator import Orchestrator
        orch = Orchestrator(self.config)
        res = orch.compare_auto_vs_manual_project(hero.hero_id, manual_path)
        
        msg = f"比較完畢！\n\nHero: {res['hero_id']}\nAuto Mesh Warnings: {res['auto_mesh_warnings']}\nManual Mesh Warnings: {res['manual_mesh_warnings']}\n\n診斷建議:\n{res['diagnosis']}"
        QMessageBox.information(self, "診斷結果", msg)

    def on_gif_postprocess(self):
        selected_heroes = self.get_selected_heroes()
        if not selected_heroes:
            QMessageBox.warning(self, "警告", "請先勾選至少一個 Hero！")
            return
            
        hero_ids = [h.hero_id for h in selected_heroes]
        logger.info(f"[ManualGIFPost] Starting post-process for: {', '.join(hero_ids)}")
        
        from src.services.orchestrator import Orchestrator
        orch = Orchestrator(self.config)
        res = orch.run_gif_postprocess(hero_ids)
        
        inspected_count = 0
        for item in res.get("items", []):
            if item.get("status") == "success":
                inspected_count += len(item.get("result", {}).get("items", []))
                
        QMessageBox.information(self, "檢查完畢", f"已完成 {len(hero_ids)} 個 Hero 的 GIF 檢查。\n共檢查了 {inspected_count} 個 GIF。\n安全策略：不刪除、不覆寫任何 GIF 幀。")

    def on_force_trim_idle(self):
        selected_heroes = self.get_selected_heroes()
        if not selected_heroes:
            QMessageBox.warning(self, "警告", "請先勾選至少一個 Hero！")
            return
            
        hero_ids = [h.hero_id for h in selected_heroes]
        logger.info(f"[ManualGIFPost] Starting idle leading-frame inspection for: {', '.join(hero_ids)}")
        
        from src.services.orchestrator import Orchestrator
        orch = Orchestrator(self.config)
        res = orch.force_trim_idle(hero_ids, trim_count=self.sb_idle_trim.value())
        
        success_gif_count = 0
        for hero_res in res.get("items", []):
            for post_res in hero_res.get("all_results", []):
                if post_res.get("status") in ["success", "preserved", "preserved_with_leading_frame_warning"]:
                    success_gif_count += 1
                
        QMessageBox.information(self, "檢查完畢", f"已完成 {len(hero_ids)} 個 Hero 的 idle GIF 檢查。\n成功檢查 {success_gif_count} 個 GIF 檔案。\n安全策略：不刪除、不覆寫任何 GIF 幀。\n詳情請見 Log 面板。")
        
    def on_open_spine_project(self):
        selected_heroes = self.get_selected_heroes()
        if not selected_heroes:
            QMessageBox.warning(self, "警告", "請先勾選至少一個 Hero！")
            return
            
        hero = selected_heroes[0]
        project_file = self._find_latest_hero_file(hero.hero_id, "spine_project", f"{hero.hero_id}.spine")
        
        if not project_file:
            QMessageBox.warning(self, "警告", "找不到專案檔，請先執行一次包含 GIF 產生的動作。")
            return
            
        try:
            import os
            # We use os.startfile so the system uses the default associated app (Spine GUI) to open the .spine file
            os.startfile(str(project_file))
            logger.info(f"Opened {project_file} with default Spine application.")
        except Exception as e:
            logger.error(f"Failed to open Spine project: {e}")
            QMessageBox.warning(self, "錯誤", f"無法開啟 Spine 專案檔: {e}")
            
    def on_inspect_spine_project(self):
        selected_heroes = self.get_selected_heroes()
        if not selected_heroes:
            QMessageBox.warning(self, "警告", "請先勾選至少一個 Hero！")
            return
            
        hero = selected_heroes[0]
        project_file = self._find_latest_hero_file(hero.hero_id, "spine_project", f"{hero.hero_id}.spine")
        
        if not project_file:
            QMessageBox.warning(self, "警告", "找不到專案檔，請先執行一次包含 GIF 產生的動作。")
            return
            
        from src.services.spine_service import SpineService
        svc = SpineService(self.config.spine_cli_path, self.config)
        svc.inspect_spine_project(project_file)
        QMessageBox.information(self, "檢查完畢", "檢查結果已寫入 Log，請查看 Log 面板。")
        logger.info("Run operation finished.")

    def on_open_output(self):
        path = self.config.output_root
        if not path:
            return
        Path(path).mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def on_clear_log(self):
        self.txt_log.clear()

    def on_open_hero_folder(self):
        selected_heroes = self.get_selected_heroes()
                
        if not selected_heroes:
            QMessageBox.warning(self, "警告", "請先勾選至少一個 Hero！")
            return
            
        output_root = Path(self.config.output_root)
        if self.config.assetstudio_use_absolute_output_path:
            output_root = output_root.resolve()
            
        runs_dir = output_root / "runs"
        if not runs_dir.exists():
            # If no runs yet, try to open the base heroes folder if it exists
            hero_dir = output_root / "heroes" / selected_heroes[0].hero_id
            if hero_dir.exists():
                os.startfile(str(hero_dir))
            return
            
        runs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
        
        if len(selected_heroes) >= 1:
            hero_id = selected_heroes[0].hero_id
            for run in runs:
                hero_dir = run / "heroes" / hero_id
                if hero_dir.exists():
                    os.startfile(str(hero_dir))
                    return
            
            # Fallback to direct heroes folder
            hero_dir = output_root / "heroes" / hero_id
            if hero_dir.exists():
                os.startfile(str(hero_dir))
                return
        else:
            if runs:
                os.startfile(str(runs[0] / "heroes"))

    def _find_latest_hero_file(self, hero_id: str, folder: str, filename: str) -> Path | None:
        output_root = Path(self.config.output_root)
        if self.config.assetstudio_use_absolute_output_path:
            output_root = output_root.resolve()

        direct = output_root / "heroes" / hero_id / folder / filename
        if direct.exists():
            return direct

        runs_dir = output_root / "runs"
        if not runs_dir.exists():
            return None

        runs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
        for run in runs:
            candidate = run / "heroes" / hero_id / folder / filename
            if candidate.exists():
                return candidate
        return None

    def append_log(self, text):
        self.txt_log.append(text)
