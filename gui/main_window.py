from PyQt6.QtWidgets import QMainWindow, QTabWidget, QLabel

from gui.sort_tab import SortTab
from gui.rename_tab import RenameTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Фотоархів — сортування, перейменування, дублікати")
        self.resize(1000, 700)

        tabs = QTabWidget()
        tabs.addTab(SortTab(), "Сортування фото")
        tabs.addTab(RenameTab(), "Перейменування папок")

        placeholder = QLabel("Модуль пошуку дублікатів — наступний етап розробки.")
        placeholder.setContentsMargins(20, 20, 20, 20)
        tabs.addTab(placeholder, "Пошук дублікатів (скоро)")

        self.setCentralWidget(tabs)
