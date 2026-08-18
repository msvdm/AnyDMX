"""AnyDMX entry point: Art-Net in -> USB DMX out."""

import sys

from PySide6.QtWidgets import QApplication

from src.gui.main_window import MainWindow
from src.gui.styles import APP_QSS


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AnyDMX")
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
