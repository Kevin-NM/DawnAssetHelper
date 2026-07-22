import sys
import os

def check_dependencies():
    missing = []
    try:
        import PySide6
    except ImportError:
        missing.append("PySide6")
        
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        missing.append("Pillow")
        
    if missing:
        print("="*40)
        print(f"[ERROR] Missing dependencies: {', '.join(missing)}")
        print("Please run:")
        print("venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        print("="*40)
        
        # If we have PySide6, we can show a message box, but if it's missing, we can't.
        # Since Pillow might be missing but PySide6 present, let's try to show a GUI error if possible.
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            # We need an app instance to show a message box
            app = QApplication(sys.argv)
            QMessageBox.critical(None, "啟動失敗 - 缺少套件", 
                f"缺少必要套件: {', '.join(missing)}\n\n請執行 open.bat 重新安裝依賴，或手動執行:\nvenv\\Scripts\\python.exe -m pip install -r requirements.txt")
            return False
        except:
            # Fallback to console
            return False
    return True

def main():
    if not check_dependencies():
        sys.exit(1)

    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
        
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
