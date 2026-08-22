"""AnyDMX entry point: Art-Net in -> USB DMX out."""

import sys

from src.core import vnet


def main():
    # Elevated helper mode: one adapter operation, no GUI. Checked before Qt
    # loads so the UAC child stays as small and quick as possible.
    if vnet.HELPER_FLAG in sys.argv:
        return vnet.helper_main(sys.argv)

    # Answered before Qt loads too, and for a plainer reason: "which version
    # are you running" is the first question on every bug report, and a
    # downloaded binary is the one copy whose version nobody can look up.
    if "--version" in sys.argv or "-V" in sys.argv:
        from src import __version__
        print(f"AnyDMX {__version__}")
        return 0

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from src import __version__
    from src.gui.main_window import MainWindow
    from src.gui.styles import APP_QSS
    from src.utils.paths import resource_dir

    app = QApplication(sys.argv)
    app.setApplicationName("AnyDMX")
    app.setApplicationVersion(__version__)
    # The window draws its own title bar, so this icon is what the taskbar,
    # the window list and the alt-tab switcher show. Missing, they show a
    # generic placeholder and the app looks unfinished before it has done
    # anything at all.
    icon = resource_dir() / "assets" / "AnyDMX.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())
