import argparse
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer
from .ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description="Voix des Douze — lecteur local macOS")
    parser.add_argument("--demo", action="store_true", help="Images synthétiques indépendantes de Dofus")
    parser.add_argument("--image", type=Path, help="Capture fixe à reconnaître")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Voix des Douze")
    app.setOrganizationName("VoixDesDouze")
    try:
        window = MainWindow()
    except Exception as exc:
        QMessageBox.critical(None, "Démarrage impossible", str(exc))
        return 1
    window.show()
    if args.demo:
        QTimer.singleShot(200, window.start_demo)
    elif args.image:
        QTimer.singleShot(200, lambda: window.load_image(args.image))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
