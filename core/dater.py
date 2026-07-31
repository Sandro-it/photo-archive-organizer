"""
dater.py — визначення дати фото/відео за пріоритетом:

    1. EXIF (дата зйомки)
    2. Дата в імені файлу (IMG_20240615, 15.06.2024, ...)
    3. Дата в імені батьківської папки (файл вже лежить у папці
       на кшталт "15_06_2022_unsorted" або просто "2022")
    4. Нічого не знайдено -> unresolved (папка "unsorted")

Свідомо НЕ використовуємо mtime/ctime файлу — ця дата означає
"коли файл потрапив на цей диск", а не "коли зроблено фото", і
може бути хибною при повторному копіюванні через роки.
"""

import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import exifread
    HAS_EXIFREAD = True
except ImportError:
    HAS_EXIFREAD = False


PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.heic', '.png', '.raw', '.cr2', '.nef', '.dng', '.tiff', '.tif', '.bmp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.3gp', '.m4v'}
ALL_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

DATE_PATTERNS = [
    r'(\d{4})[-_](\d{2})[-_](\d{2})',   # 2024-06-15 / 2024_06_15
    r'(\d{4})(\d{2})(\d{2})',           # 20240615
    r'(\d{2})\.(\d{2})\.(\d{4})',       # 15.06.2024
    r'(\d{2})_(\d{2})_(\d{4})',         # 15_06_2024  (день_місяць_рік)
]


@dataclass
class DateResult:
    date: datetime | None
    source: str  # 'exif' | 'filename' | 'parent_folder' | 'unresolved'


def _parse_date_from_text(text: str) -> datetime | None:
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        try:
            if len(groups[0]) == 4:
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            else:
                day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
            if 1990 <= year <= 2035 and 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day)
        except ValueError:
            continue
    return None


def _get_exif_date(filepath: Path) -> datetime | None:
    if filepath.suffix.lower() not in PHOTO_EXTENSIONS:
        return None

    if HAS_PIL:
        try:
            with Image.open(filepath) as img:
                exif_data = img._getexif()
                if exif_data:
                    for tag_id, value in exif_data.items():
                        tag = TAGS.get(tag_id, tag_id)
                        if tag in ('DateTimeOriginal', 'DateTime', 'DateTimeDigitized'):
                            return datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
        except Exception:
            pass

    if HAS_EXIFREAD:
        try:
            with open(filepath, 'rb') as f:
                tags = exifread.process_file(f, stop_tag='EXIF DateTimeOriginal', details=False)
                for key in ('EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime'):
                    if key in tags:
                        return datetime.strptime(str(tags[key]), '%Y:%m:%d %H:%M:%S')
        except Exception:
            pass

    return None


def resolve_date(filepath: Path) -> DateResult:
    """Визначає дату файлу за встановленим пріоритетом джерел."""

    date = _get_exif_date(filepath)
    if date:
        return DateResult(date, 'exif')

    date = _parse_date_from_text(filepath.stem)
    if date:
        return DateResult(date, 'filename')

    # Йдемо по батьківських папках знизу вгору (до 3 рівнів), шукаючи дату в назві
    parent = filepath.parent
    for _ in range(3):
        date = _parse_date_from_text(parent.name)
        if date:
            return DateResult(date, 'parent_folder')
        if parent.parent == parent:
            break
        parent = parent.parent

    return DateResult(None, 'unresolved')
