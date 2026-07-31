"""
duplicates_tab.py — GUI для пошуку дублікатів у вже проіндексованому архіві.

Працює з готовим SQLite-індексом (core/duplicates.py), нічого на диску
не сканує повторно. Видалення — виключно через send2trash (кошик),
permanent delete тут немає навмисно.
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
    QLineEdit, QFileDialog, QMessageBox, QScrollArea, QRadioButton, QButtonGroup
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, pyqtSignal

from send2trash import send2trash as send_to_trash

from core.db import get_db_path
from core.duplicates import DuplicateGroup
from gui.workers import FindDuplicatesWorker


THUMB_SIZE = 150


class DuplicateGroupCard(QGroupBox):
    """Одна група дублікатів: мініатюри всіх файлів, рекомендований позначений
    зіркою й рамкою, радіо-кнопки для ручного вибору, що саме лишити."""

    skipped = pyqtSignal()
    resolved = pyqtSignal()

    def __init__(self, group: DuplicateGroup, title_prefix: str, parent=None):
        super().__init__(f"{title_prefix} — {len(group.files)} файл(ів)", parent)
        self.group = group
        self.radio_by_path: dict[QRadioButton, Path] = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        thumbs_layout = QHBoxLayout()
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        recommended_path = self.group.recommended.path

        for file_score in self.group.files:
            item_layout = QVBoxLayout()
            is_recommended = file_score.path == recommended_path

            if is_recommended:
                star_label = QLabel("★ Рекомендовано")
                star_label.setStyleSheet("color: #b8860b; font-weight: bold;")
                item_layout.addWidget(star_label)

            thumb_label = QLabel()
            thumb_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(file_score.path))
            border = "border: 3px solid #b8860b;" if is_recommended else "border: 1px solid #999999;"
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    THUMB_SIZE, THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                thumb_label.setPixmap(pixmap)
            else:
                thumb_label.setText("(не вдалось\nзавантажити прев'ю)")
            thumb_label.setStyleSheet(border)
            item_layout.addWidget(thumb_label)

            name_label = QLabel(file_score.path.name)
            name_label.setWordWrap(True)
            name_label.setMaximumWidth(THUMB_SIZE)
            item_layout.addWidget(name_label)

            info_label = QLabel(f"бал: {file_score.score}")
            info_label.setToolTip("\n".join(file_score.reasons))
            item_layout.addWidget(info_label)

            radio = QRadioButton("Залишити цей")
            radio.setChecked(is_recommended)
            self.button_group.addButton(radio)
            self.radio_by_path[radio] = file_score.path
            item_layout.addWidget(radio)

            thumbs_layout.addLayout(item_layout)

        outer.addLayout(thumbs_layout)

        buttons_layout = QHBoxLayout()
        recommended_btn = QPushButton("Залишити рекомендований")
        recommended_btn.clicked.connect(self._apply_recommended)
        apply_btn = QPushButton("Застосувати вибір")
        apply_btn.clicked.connect(self._apply_selection)
        skip_btn = QPushButton("Пропустити")
        skip_btn.clicked.connect(lambda: self.skipped.emit())
        buttons_layout.addWidget(recommended_btn)
        buttons_layout.addWidget(apply_btn)
        buttons_layout.addWidget(skip_btn)
        outer.addLayout(buttons_layout)

    def _selected_keep_path(self) -> Path | None:
        for radio, path in self.radio_by_path.items():
            if radio.isChecked():
                return path
        return None

    def _apply_recommended(self):
        for radio, path in self.radio_by_path.items():
            radio.setChecked(path == self.group.recommended.path)
        self._apply_selection()

    def _apply_selection(self):
        keep_path = self._selected_keep_path()
        if keep_path is None:
            QMessageBox.warning(self, "Не вибрано", "Виберіть файл, який лишити.")
            return

        to_trash = [f.path for f in self.group.files if f.path != keep_path]
        if not to_trash:
            self.resolved.emit()
            return

        listing = "\n".join(str(p) for p in to_trash)
        confirm = QMessageBox.question(
            self, "Підтвердження",
            f"Перемістити в кошик {len(to_trash)} файл(ів)?\n\n{listing}\n\n"
            f"Залишиться: {keep_path}",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        errors = []
        for path in to_trash:
            try:
                send_to_trash(str(path))
            except Exception as e:
                errors.append(f"{path}: {e}")

        if errors:
            QMessageBox.critical(self, "Помилки видалення", "\n".join(errors))

        self.resolved.emit()


class DuplicatesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.archive_root: Path | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Архів ---
        root_box = QGroupBox("Архів (папка, де вже побудовано індекс сортуванням)")
        root_layout = QHBoxLayout(root_box)
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        browse_btn = QPushButton("Вибрати...")
        browse_btn.clicked.connect(self._choose_root)
        root_layout.addWidget(self.root_edit)
        root_layout.addWidget(browse_btn)
        layout.addWidget(root_box)

        # --- Дії ---
        action_layout = QHBoxLayout()
        self.scan_btn = QPushButton("Знайти дублікати")
        self.scan_btn.clicked.connect(self._run_scan)
        action_layout.addWidget(self.scan_btn)
        layout.addLayout(action_layout)

        self.status_label = QLabel(
            "Виберіть папку архіву з уже побудованим індексом (вкладка «Сортування фото»)."
        )
        layout.addWidget(self.status_label)

        # --- Результати ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.addStretch()
        self.scroll_area.setWidget(self.results_container)
        layout.addWidget(self.scroll_area)

    def _choose_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Вибрати кореневу папку архіву")
        if folder:
            self.archive_root = Path(folder)
            self.root_edit.setText(folder)

    def _run_scan(self):
        if not self.archive_root:
            QMessageBox.warning(self, "Немає архіву", "Виберіть папку архіву з побудованим індексом.")
            return

        index_db_path = get_db_path(self.archive_root)
        if not index_db_path.exists():
            QMessageBox.warning(
                self, "Індекс не знайдено",
                f"Файл індексу не знайдено:\n{index_db_path}\n\n"
                "Спочатку виконайте сортування (вкладка «Сортування фото»), щоб побудувати індекс.",
            )
            return

        self._clear_results()
        self.status_label.setText("Пошук дублікатів...")
        self.scan_btn.setEnabled(False)

        self._worker = FindDuplicatesWorker(index_db_path)
        self._worker.finished_groups.connect(self._on_groups_found)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_error(self, msg: str):
        self.scan_btn.setEnabled(True)
        self.status_label.setText("Помилка під час пошуку дублікатів.")
        QMessageBox.critical(self, "Помилка", msg)

    def _clear_results(self):
        while self.results_layout.count() > 1:  # лишаємо addStretch() в кінці
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _on_groups_found(self, groups: dict):
        self.scan_btn.setEnabled(True)
        exact = groups.get('exact', [])
        similar = groups.get('similar', [])

        if not exact and not similar:
            self.status_label.setText("Дублікатів не знайдено.")
            return

        self.status_label.setText(
            f"Знайдено груп: {len(exact)} точних, {len(similar)} візуально схожих."
        )

        for group in exact:
            self._add_card(group, "Точний дублікат")
        for group in similar:
            self._add_card(group, "Візуально схожі")

    def _add_card(self, group: DuplicateGroup, title_prefix: str):
        card = DuplicateGroupCard(group, title_prefix)
        card.skipped.connect(lambda: self._remove_card(card))
        card.resolved.connect(lambda: self._remove_card(card))
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)

    def _remove_card(self, card: DuplicateGroupCard):
        card.deleteLater()
