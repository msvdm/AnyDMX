"""AnyDMX entry point: Art-Net in -> USB DMX out."""

import sys

from src.core import vnet


def main():
    # Elevated helper mode: one adapter operation, no GUI. Checked before Qt
    # loads so the UAC child stays as small and quick as possible.
    if vnet.HELPER_FLAG in sys.argv:
        return vnet.helper_main(sys.argv)

    from PySide6.QtWidgets import QApplication

    from src.gui.main_window import MainWindow
    from src.gui.styles import APP_QSS

    app = QApplication(sys.argv)
    app.setApplicationName("AnyDMX")
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())
