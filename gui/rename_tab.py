import uuid
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QFileDialog,
    QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt

from core.renamer import RenameRule, ValueSource, InsertPosition, execute_rename
from gui.workers import PlanRenameWorker


class RenameTab(QWidget):
    def __init__(self):
        super().__init__()
        self.root_folder: Path | None = None
        self.current_plan = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Коренева папка ---
        root_box = QGroupBox("Папка з підпапками для перейменування")
        root_layout = QHBoxLayout(root_box)
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        browse_btn = QPushButton("Вибрати...")
        browse_btn.clicked.connect(self._choose_root)
        self.recursive_check = QCheckBox("Рекурсивно (усі рівні вкладеності)")
        root_layout.addWidget(self.root_edit)
        root_layout.addWidget(browse_btn)
        root_layout.addWidget(self.recursive_check)
        layout.addWidget(root_box)

        # --- Конструктор правила ---
        rule_box = QGroupBox("Правило перейменування")
        rule_layout = QHBoxLayout(rule_box)

        rule_layout.addWidget(QLabel("Значення з:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Дата з поточної назви папки", ValueSource.PARSE_EXISTING_NAME)
        self.source_combo.addItem("Дата створення/зміни папки", ValueSource.FOLDER_MTIME)
        self.source_combo.addItem("Найраніше фото всередині", ValueSource.EARLIEST_PHOTO_INSIDE)
        self.source_combo.addItem("Найновіше фото всередині", ValueSource.LATEST_PHOTO_INSIDE)
        self.source_combo.addItem("Свій текст", ValueSource.MANUAL_TEXT)
        rule_layout.addWidget(self.source_combo)

        rule_layout.addWidget(QLabel("Позиція:"))
        self.position_combo = QComboBox()
        self.position_combo.addItem("На початок", InsertPosition.PREFIX)
        self.position_combo.addItem("В кінець", InsertPosition.SUFFIX)
        rule_layout.addWidget(self.position_combo)

        rule_layout.addWidget(QLabel("Шаблон:"))
        self.token_edit = QLineEdit("{MM}")
        self.token_edit.setToolTip("Токени: {DD} {MM} {YYYY} {YY}. Або звичайний текст.")
        rule_layout.addWidget(self.token_edit)

        rule_layout.addWidget(QLabel("Розділювач:"))
        self.sep_edit = QLineEdit("_")
        self.sep_edit.setMaximumWidth(40)
        rule_layout.addWidget(self.sep_edit)

        layout.addWidget(rule_box)

        # --- Умови (щоб не чіпати вже перейменоване) ---
        cond_box = QGroupBox("Умови застосування (необов'язково)")
        cond_layout = QHBoxLayout(cond_box)
        cond_layout.addWidget(QLabel("Пропустити, якщо назва відповідає regex:"))
        self.skip_pattern_edit = QLineEdit(r'^\d{2}_\d{2}_\d{2}_\d{4}_')
        self.skip_pattern_edit.setToolTip("За замовч.: пропускати вже перейменовані папки ММ_ДД_ММ_РРРР_...")
        cond_layout.addWidget(self.skip_pattern_edit)
        layout.addWidget(cond_box)

        # --- Дії ---
        action_buttons = QHBoxLayout()
        preview_btn = QPushButton("Показати прев'ю")
        preview_btn.clicked.connect(self._run_preview)
        self.execute_btn = QPushButton("Перейменувати")
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self._run_execute)
        action_buttons.addWidget(preview_btn)
        action_buttons.addWidget(self.execute_btn)
        layout.addLayout(action_buttons)

        # --- Таблиця прев'ю ---
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Було", "Стане", "Причина"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def _choose_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Вибрати папку")
        if folder:
            self.root_folder = Path(folder)
            self.root_edit.setText(folder)

    def _collect_folders(self) -> list[Path]:
        if not self.root_folder:
            return []
        if self.recursive_check.isChecked():
            return [p for p in self.root_folder.rglob('*') if p.is_dir()]
        return [p for p in self.root_folder.iterdir() if p.is_dir()]

    def _current_rule(self) -> RenameRule:
        return RenameRule(
            value_source=self.source_combo.currentData(),
            position=self.position_combo.currentData(),
            token_template=self.token_edit.text(),
            separator=self.sep_edit.text(),
            manual_value=self.token_edit.text(),
            skip_pattern=self.skip_pattern_edit.text().strip() or None,
        )

    def _run_preview(self):
        if not self.root_folder:
            QMessageBox.warning(self, "Немає папки", "Виберіть папку.")
            return
        folders = self._collect_folders()
        rule = self._current_rule()

        self._plan_worker = PlanRenameWorker(rule, folders)
        self._plan_worker.finished_plan.connect(self._on_plan_ready)
        self._plan_worker.error.connect(lambda msg: QMessageBox.critical(self, "Помилка", msg))
        self._plan_worker.start()

    def _on_plan_ready(self, plan):
        self.current_plan = plan
        self.table.setRowCount(len(plan))
        for row, item in enumerate(plan):
            self.table.setItem(row, 0, QTableWidgetItem(item.old_path.name))
            self.table.setItem(row, 1, QTableWidgetItem(item.new_path.name))
            self.table.setItem(row, 2, QTableWidgetItem(item.reason))
        self.status_label.setText(f"Знайдено {len(plan)} папок для перейменування.")
        self.execute_btn.setEnabled(len(plan) > 0)

    def _run_execute(self):
        if not self.current_plan:
            return
        confirm = QMessageBox.question(self, "Підтвердження", f"Перейменувати {len(self.current_plan)} папок?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        stats = execute_rename(self.current_plan)
        QMessageBox.information(self, "Готово", f"Перейменовано: {stats['done']}, помилок: {stats['errors']}")
        self.current_plan = []
        self.table.setRowCount(0)
        self.execute_btn.setEnabled(False)
