import uuid
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QListWidget, QPushButton,
    QLabel, QLineEdit, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QFileDialog, QProgressBar, QMessageBox
)
from PyQt6.QtCore import Qt

from core.sorter import SortConfig, GroupingTemplate, FileAction, ConflictPolicy
from core.db import get_db_path
from core.undo import undo_batch
from gui.workers import PlanSortWorker, ExecuteSortWorker


class SortTab(QWidget):
    def __init__(self):
        super().__init__()
        self.sources: list[Path] = []
        self.dest_root: Path | None = None
        self.current_plan = []
        self.last_batch_id: str | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Джерело ---
        src_box = QGroupBox("Звідки (можна кілька папок)")
        src_layout = QVBoxLayout(src_box)
        self.src_list = QListWidget()
        src_layout.addWidget(self.src_list)
        src_buttons = QHBoxLayout()
        add_src_btn = QPushButton("Додати папку...")
        add_src_btn.clicked.connect(self._add_source)
        remove_src_btn = QPushButton("Прибрати вибрану")
        remove_src_btn.clicked.connect(self._remove_source)
        self.recursive_check = QCheckBox("Включно з підпапками")
        self.recursive_check.setChecked(True)
        src_buttons.addWidget(add_src_btn)
        src_buttons.addWidget(remove_src_btn)
        src_buttons.addWidget(self.recursive_check)
        src_layout.addLayout(src_buttons)
        layout.addWidget(src_box)

        # --- Призначення ---
        dest_box = QGroupBox("Куди (коренева папка архіву, де вже є роки)")
        dest_layout = QHBoxLayout(dest_box)
        self.dest_edit = QLineEdit()
        self.dest_edit.setReadOnly(True)
        dest_browse_btn = QPushButton("Вибрати...")
        dest_browse_btn.clicked.connect(self._choose_dest)
        dest_layout.addWidget(self.dest_edit)
        dest_layout.addWidget(dest_browse_btn)
        layout.addWidget(dest_box)

        # --- Опції ---
        opt_box = QGroupBox("Опції")
        opt_layout = QHBoxLayout(opt_box)

        opt_layout.addWidget(QLabel("Структура:"))
        self.grouping_combo = QComboBox()
        self.grouping_combo.addItem("Рік / День_Місяць_Рік_label", GroupingTemplate.YEAR_DATE)
        self.grouping_combo.addItem("Рік / Місяць / День_Місяць_Рік_label", GroupingTemplate.YEAR_MONTH_DATE)
        self.grouping_combo.addItem("Тільки Рік / label", GroupingTemplate.YEAR_ONLY)
        opt_layout.addWidget(self.grouping_combo)

        opt_layout.addWidget(QLabel("Дія:"))
        self.action_combo = QComboBox()
        self.action_combo.addItem("Копіювати -> перевірити -> видалити оригінал (безпечно)", FileAction.COPY_VERIFY_DELETE)
        self.action_combo.addItem("Копіювати", FileAction.COPY)
        self.action_combo.addItem("Перемістити", FileAction.MOVE)
        opt_layout.addWidget(self.action_combo)

        opt_layout.addWidget(QLabel("Мітка:"))
        self.label_edit = QLineEdit("unsorted")
        opt_layout.addWidget(self.label_edit)

        layout.addWidget(opt_box)

        # --- Дії ---
        action_buttons = QHBoxLayout()
        preview_btn = QPushButton("Показати прев'ю (dry-run)")
        preview_btn.clicked.connect(self._run_preview)
        self.execute_btn = QPushButton("Виконати")
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self._run_execute)
        self.undo_btn = QPushButton("Відкатити останній прогін")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._run_undo)
        action_buttons.addWidget(preview_btn)
        action_buttons.addWidget(self.execute_btn)
        action_buttons.addWidget(self.undo_btn)
        layout.addLayout(action_buttons)

        # --- Прев'ю таблиця ---
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Файл", "-> Куди", "Джерело дати"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ---------- дії користувача ----------

    def _add_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Вибрати папку-джерело")
        if folder:
            self.sources.append(Path(folder))
            self.src_list.addItem(folder)

    def _remove_source(self):
        row = self.src_list.currentRow()
        if row >= 0:
            self.src_list.takeItem(row)
            del self.sources[row]

    def _choose_dest(self):
        folder = QFileDialog.getExistingDirectory(self, "Вибрати кореневу папку призначення")
        if folder:
            self.dest_root = Path(folder)
            self.dest_edit.setText(folder)

    def _current_config(self) -> SortConfig | None:
        if not self.sources:
            QMessageBox.warning(self, "Немає джерела", "Додайте хоча б одну папку-джерело.")
            return None
        if not self.dest_root:
            QMessageBox.warning(self, "Немає призначення", "Виберіть кореневу папку призначення.")
            return None

        label = self.label_edit.text().strip() or "unsorted"
        return SortConfig(
            sources=self.sources,
            dest_root=self.dest_root,
            recursive=self.recursive_check.isChecked(),
            grouping=self.grouping_combo.currentData(),
            action=self.action_combo.currentData(),
            conflict_policy=ConflictPolicy.RENAME_WITH_SUFFIX,
            unresolved_label=label,
            event_label=label,
        )

    def _run_preview(self):
        cfg = self._current_config()
        if not cfg:
            return
        self.status_label.setText("Сканування...")
        self._plan_worker = PlanSortWorker(cfg)
        self._plan_worker.finished_plan.connect(self._on_plan_ready)
        self._plan_worker.error.connect(lambda msg: QMessageBox.critical(self, "Помилка", msg))
        self._plan_worker.start()
        self._last_cfg = cfg

    def _on_plan_ready(self, plan):
        self.current_plan = plan
        self.table.setRowCount(len(plan))
        for row, item in enumerate(plan):
            self.table.setItem(row, 0, QTableWidgetItem(str(item.src)))
            self.table.setItem(row, 1, QTableWidgetItem(str(item.dst)))
            self.table.setItem(row, 2, QTableWidgetItem(item.date_source))
        self.status_label.setText(f"Знайдено {len(plan)} файлів. Перевірте прев'ю і натисніть «Виконати».")
        self.execute_btn.setEnabled(len(plan) > 0)

    def _run_execute(self):
        if not self.current_plan:
            return
        confirm = QMessageBox.question(
            self, "Підтвердження",
            f"Обробити {len(self.current_plan)} файлів? Дію: {self._last_cfg.action.value}",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.last_batch_id = str(uuid.uuid4())
        index_db_path = get_db_path(self._last_cfg.dest_root)
        self.progress.setMaximum(len(self.current_plan))

        self._exec_worker = ExecuteSortWorker(self._last_cfg, self.current_plan, index_db_path, self.last_batch_id)
        self._exec_worker.progress.connect(lambda i, total: self.progress.setValue(i))
        self._exec_worker.finished_stats.connect(self._on_execute_done)
        self._exec_worker.error.connect(lambda msg: QMessageBox.critical(self, "Помилка", msg))
        self._exec_worker.start()

    def _on_execute_done(self, stats):
        self.status_label.setText(
            f"Готово: {stats['done']} оброблено, {stats['skipped']} пропущено, {stats['errors']} помилок."
        )
        self.undo_btn.setEnabled(True)

    def _run_undo(self):
        if not self.last_batch_id or not self._last_cfg:
            return
        confirm = QMessageBox.question(self, "Відкат", "Повернути файли на початкові місця?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        index_db_path = get_db_path(self._last_cfg.dest_root)
        stats = undo_batch(index_db_path, self.last_batch_id)
        QMessageBox.information(self, "Відкат виконано", f"Повернуто: {stats['reverted']}, помилок: {stats['errors']}")
        self.undo_btn.setEnabled(False)
