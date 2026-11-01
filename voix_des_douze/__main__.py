import argparse
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from .ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description="Voix des Douze — lecteur local macOS")
    parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Voix des Douze")
    app.setOrganizationName("VoixDesDouze")
    try:
        window = MainWindow()
    except Exception as exc:
        QMessageBox.critical(None, "Démarrage impossible", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
