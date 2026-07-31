"""
renamer.py — конструктор правил перейменування папок/файлів.

Правило складається з чотирьох незалежних частин, що дозволяє
покрити нові варіанти пізніше без переписування коду:

  1. value_source  — звідки береться дата/значення для вставки
  2. position       — куди його вставити відносно наявної назви
  3. skip_pattern   — regex, який позначає "вже перейменовано, пропустити"
  4. apply_pattern  — regex, якому назва має відповідати, щоб правило застосувалось
"""

import re
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from .dater import resolve_date


class ValueSource(Enum):
    PARSE_EXISTING_NAME = "parse_existing_name"   # витягнути дату з поточної назви папки
    FOLDER_MTIME = "folder_mtime"                  # дата створення/модифікації папки
    EARLIEST_PHOTO_INSIDE = "earliest_photo"       # наймолодша дата фото всередині
    LATEST_PHOTO_INSIDE = "latest_photo"           # найновіша дата фото всередині
    MANUAL_TEXT = "manual_text"                    # довільний текст, заданий вручну


class InsertPosition(Enum):
    PREFIX = "prefix"
    SUFFIX = "suffix"
    REPLACE_MATCH = "replace_match"   # замінити знайдений apply_pattern на новий текст


# День/місяць/рік у назві папки — типовий формат у цьому архіві
_NAME_DATE_PATTERN = re.compile(r'^(\d{2})_(\d{2})_(\d{4})_(.+)$')


@dataclass
class RenameRule:
    value_source: ValueSource
    position: InsertPosition
    token_template: str = "{MM}"       # напр. "{MM}", "{DD}_{MM}", довільний текст
    separator: str = "_"
    manual_value: str = ""             # використовується, якщо value_source == MANUAL_TEXT
    apply_pattern: str | None = None   # regex: застосувати тільки якщо назва відповідає
    skip_pattern: str | None = None    # regex: пропустити, якщо назва вже відповідає


@dataclass
class PlannedRename:
    old_path: Path
    new_path: Path
    reason: str   # чому саме таке нове ім'я (для прев'ю користувачу)


def _folder_date_from_photos(folder: Path, earliest: bool) -> datetime | None:
    dates = []
    for f in folder.rglob('*'):
        if f.is_file():
            result = resolve_date(f)
            if result.date:
                dates.append(result.date)
    if not dates:
        return None
    return min(dates) if earliest else max(dates)


def _resolve_token_value(rule: RenameRule, folder: Path) -> str | None:
    """Обчислює текст, який треба вставити, згідно з value_source."""
    date = None

    if rule.value_source == ValueSource.MANUAL_TEXT:
        return rule.manual_value

    if rule.value_source == ValueSource.PARSE_EXISTING_NAME:
        match = _NAME_DATE_PATTERN.match(folder.name)
        if not match:
            return None
        day, month, year, _ = match.groups()
        date = datetime(int(year), int(month), int(day))

    elif rule.value_source == ValueSource.FOLDER_MTIME:
        date = datetime.fromtimestamp(folder.stat().st_mtime)

    elif rule.value_source == ValueSource.EARLIEST_PHOTO_INSIDE:
        date = _folder_date_from_photos(folder, earliest=True)

    elif rule.value_source == ValueSource.LATEST_PHOTO_INSIDE:
        date = _folder_date_from_photos(folder, earliest=False)

    if date is None:
        return None

    return rule.token_template.format(
        DD=f"{date.day:02d}", MM=f"{date.month:02d}",
        YYYY=date.year, YY=f"{date.year % 100:02d}",
    )


def plan_rename(rule: RenameRule, folders: list[Path]) -> list[PlannedRename]:
    """Розраховує нові імена папок без виконання (для прев'ю з чекбоксами в GUI)."""
    plan = []

    for folder in folders:
        name = folder.name

        if rule.skip_pattern and re.match(rule.skip_pattern, name):
            continue
        if rule.apply_pattern and not re.match(rule.apply_pattern, name):
            continue

        value = _resolve_token_value(rule, folder)
        if value is None:
            continue  # не вдалось визначити значення -> не чіпаємо папку

        if rule.position == InsertPosition.PREFIX:
            new_name = f"{value}{rule.separator}{name}"
        elif rule.position == InsertPosition.SUFFIX:
            new_name = f"{name}{rule.separator}{value}"
        else:  # REPLACE_MATCH
            if not rule.apply_pattern:
                continue
            new_name = re.sub(rule.apply_pattern, str(value), name)

        if new_name == name:
            continue

        plan.append(PlannedRename(
            old_path=folder,
            new_path=folder.parent / new_name,
            reason=f"{rule.value_source.value} -> {value}",
        ))

    return plan


def execute_rename(plan: list[PlannedRename], db_conn=None, batch_id: str | None = None) -> dict:
    """Виконує перейменування, логуючи кожну дію (якщо передано з'єднання з індексом)."""
    from . import db as db_module

    stats = {'done': 0, 'errors': 0}
    for item in plan:
        try:
            item.old_path.rename(item.new_path)
            if db_conn is not None and batch_id:
                db_module.log_operation(db_conn, 'rename', str(item.old_path), str(item.new_path), batch_id)
            stats['done'] += 1
        except Exception:
            stats['errors'] += 1
    return stats
