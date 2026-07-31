from PyQt6.QtWidgets import QMainWindow, QTabWidget

from gui.sort_tab import SortTab
from gui.rename_tab import RenameTab
from gui.duplicates_tab import DuplicatesTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Фотоархів — сортування, перейменування, дублікати")
        self.resize(1000, 700)

        tabs = QTabWidget()
        tabs.addTab(SortTab(), "Сортування фото")
        tabs.addTab(RenameTab(), "Перейменування папок")
        tabs.addTab(DuplicatesTab(), "Пошук дублікатів")

        self.setCentralWidget(tabs)
