import logging
from pathlib import Path
from PySide6.QtCore import QObject, Signal

class LoggerService(QObject):
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("DawnAssetHelper")
        self.logger.setLevel(logging.DEBUG)
        self.file_handler = None

    def setup_run_logger(self, log_dir: Path, run_id: str):
        if self.file_handler:
            self.logger.removeHandler(self.file_handler)

        log_file = log_dir / "run.log"
        self.file_handler = logging.FileHandler(log_file, encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        self.file_handler.setFormatter(formatter)
        self.logger.addHandler(self.file_handler)

    def log(self, message: str, level: int = logging.INFO):
        self.logger.log(level, message)
        level_name = logging.getLevelName(level)
        self.log_signal.emit(f"[{level_name}] {message}")

    def info(self, message: str):
        self.log(message, logging.INFO)

    def warning(self, message: str):
        self.log(message, logging.WARNING)

    def error(self, message: str):
        self.log(message, logging.ERROR)

logger = LoggerService()
