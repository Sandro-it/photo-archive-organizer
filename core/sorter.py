"""
sorter.py — розкладання файлів у структуру призначення.

Головні принципи, узгоджені з користувачем:
  - Якщо цільова папка року/дати вже існує на диску призначення —
    класти файли ТУДИ, а не створювати нову поруч.
  - Дата файлу НЕ визначена (unresolved) -> окрема папка "unsorted"
    в корені призначення, без спроб вгадати з дати модифікації.
  - Кожна операція логується в SQLite (таблиця operations) з
    batch_id, щоб можна було відкотити весь прогін одним кліком.
"""

import shutil
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

from . import db
from .dater import resolve_date


class GroupingTemplate(Enum):
    YEAR_ONLY = "year_only"                 # DEST/2022/label
    YEAR_DATE = "year_date"                 # DEST/2022/06_15_06_2022_label (за замовчуванням)
    YEAR_MONTH_DATE = "year_month_date"     # DEST/2022/06/06_15_06_2022_label


class FileAction(Enum):
    MOVE = "move"
    COPY = "copy"
    COPY_VERIFY_DELETE = "copy_verify_delete"   # безпечний режим для першого прогону


class ConflictPolicy(Enum):
    RENAME_WITH_SUFFIX = "rename_suffix"
    SKIP = "skip"
    OVERWRITE = "overwrite"   # не рекомендується, доступно явно


@dataclass
class SortConfig:
    sources: list[Path]
    dest_root: Path
    recursive: bool = True
    grouping: GroupingTemplate = GroupingTemplate.YEAR_DATE
    action: FileAction = FileAction.COPY_VERIFY_DELETE
    conflict_policy: ConflictPolicy = ConflictPolicy.RENAME_WITH_SUFFIX
    unresolved_label: str = "unsorted"
    event_label: str = "unsorted"   # мітка для розпізнаних дат, якщо користувач не задав іншу


@dataclass
class PlannedMove:
    src: Path
    dst: Path
    date_source: str   # 'exif' | 'filename' | 'parent_folder' | 'unresolved'
    resolved_date: datetime | None


def build_dest_path(cfg: SortConfig, date: datetime | None) -> Path:
    """Формат назви папки — ММ_ДД_ММ_РРРР_label (місяць повторюється спереду,
    щоб файловий менеджер сортував за місяцем, як і вже перейменовані папки
    в core/renamer.py). Наприклад, рік=2025, місяць=03, день=15 -> папка
    "03_15_03_2025_unsorted", яка за GroupingTemplate.YEAR_DATE (типово)
    лежить одразу в DEST/2025/, без проміжної папки-місяця."""
    if date is None:
        return cfg.dest_root / cfg.unresolved_label

    year_folder = cfg.dest_root / str(date.year)
    event_name = f"{date.month:02d}_{date.day:02d}_{date.month:02d}_{date.year}_{cfg.event_label}"

    if cfg.grouping == GroupingTemplate.YEAR_ONLY:
        return year_folder
    if cfg.grouping == GroupingTemplate.YEAR_MONTH_DATE:
        return year_folder / f"{date.month:02d}" / event_name
    return year_folder / event_name  # YEAR_DATE (за замовчуванням, без проміжної папки-місяця)


def _resolve_conflict(dest_file: Path, policy: ConflictPolicy) -> Path | None:
    """Повертає фінальний шлях файлу, або None якщо файл треба пропустити."""
    if not dest_file.exists():
        return dest_file

    if policy == ConflictPolicy.OVERWRITE:
        return dest_file
    if policy == ConflictPolicy.SKIP:
        return None

    stem, suffix = dest_file.stem, dest_file.suffix
    counter = 1
    candidate = dest_file
    while candidate.exists():
        candidate = dest_file.parent / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def plan_sort(cfg: SortConfig, files: list[Path]) -> list[PlannedMove]:
    """Розраховує, куди піде кожен файл, без виконання дій (для прев'ю в GUI)."""
    plan = []
    for filepath in files:
        date_result = resolve_date(filepath)
        dest_folder = build_dest_path(cfg, date_result.date)
        dest_file = dest_folder / filepath.name
        plan.append(PlannedMove(filepath, dest_file, date_result.source, date_result.date))
    return plan


def execute_sort(
    cfg: SortConfig,
    plan: list[PlannedMove],
    index_db_path: Path,
    batch_id: str,
    progress_cb: Callable[[int, int, PlannedMove], None] | None = None,
) -> dict:
    """Виконує заплановані переміщення/копіювання, логуючи кожну дію."""
    stats = {'done': 0, 'skipped': 0, 'errors': 0}

    with db.open_db(index_db_path) as conn:
        for i, item in enumerate(plan, start=1):
            try:
                final_dst = _resolve_conflict(item.dst, cfg.conflict_policy)
                if final_dst is None:
                    stats['skipped'] += 1
                    continue

                final_dst.parent.mkdir(parents=True, exist_ok=True)

                if cfg.action == FileAction.MOVE:
                    shutil.move(str(item.src), str(final_dst))
                elif cfg.action == FileAction.COPY:
                    shutil.copy2(item.src, final_dst)
                else:  # COPY_VERIFY_DELETE
                    shutil.copy2(item.src, final_dst)
                    if final_dst.stat().st_size == item.src.stat().st_size:
                        item.src.unlink()
                    else:
                        raise IOError("Розмір копії не збігається з оригіналом — оригінал НЕ видалено")

                db.log_operation(conn, cfg.action.value, str(item.src), str(final_dst), batch_id)
                stats['done'] += 1

            except Exception as e:
                stats['errors'] += 1
                if progress_cb:
                    progress_cb(i, len(plan), item)
                continue

            if progress_cb:
                progress_cb(i, len(plan), item)

    return stats
