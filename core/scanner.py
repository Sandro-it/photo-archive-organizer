"""
scanner.py — обхід файлової системи і побудова SQLite-індексу.

Рахує SHA-256 (для точних дублікатів) та perceptual hash
(для візуально схожих копій — модуль дублікатів використає це
пізніше). Індексація важка операція, тому винесена в окрему
функцію з callback-ом прогресу, щоб GUI міг показати прогрес-бар
і виконувати це у фоновому потоці.
"""

import hashlib
from pathlib import Path
from typing import Callable, Iterable

from . import db
from .dater import resolve_date, ALL_EXTENSIONS, PHOTO_EXTENSIONS

try:
    from PIL import Image
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False


def _sha256_of(filepath: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            h.update(chunk)
    return h.hexdigest()


def _image_meta(filepath: Path) -> dict:
    """Ширина/висота і perceptual hash — тільки для растрових фото."""
    meta = {'width': None, 'height': None, 'phash': None}
    if filepath.suffix.lower() not in PHOTO_EXTENSIONS or not HAS_IMAGEHASH:
        return meta
    try:
        with Image.open(filepath) as img:
            meta['width'], meta['height'] = img.size
            meta['phash'] = str(imagehash.phash(img))
    except Exception:
        pass
    return meta


def collect_files(sources: Iterable[Path], recursive: bool = True) -> list[Path]:
    """Збирає файли фото/відео з однієї або кількох папок-джерел."""
    files = []
    for source in sources:
        source = Path(source)
        iterator = source.rglob('*') if recursive else source.glob('*')
        for f in iterator:
            if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS:
                files.append(f)
    return sorted(set(files))


def build_index(
    sources: Iterable[Path],
    index_db_path: Path,
    recursive: bool = True,
    progress_cb: Callable[[int, int, Path], None] | None = None,
) -> dict:
    """
    Сканує джерела і (пере)будує записи в SQLite-індексі.
    progress_cb(current, total, current_path) викликається після кожного файлу.
    Повертає статистику по джерелах визначення дати.
    """
    files = collect_files(sources, recursive=recursive)
    stats = {'exif': 0, 'filename': 0, 'parent_folder': 0, 'unresolved': 0}

    with db.open_db(index_db_path) as conn:
        for i, filepath in enumerate(files, start=1):
            date_result = resolve_date(filepath)
            stats[date_result.source] += 1

            meta = _image_meta(filepath)

            db.upsert_file(
                conn,
                path=str(filepath),
                size=filepath.stat().st_size,
                sha256=_sha256_of(filepath),
                phash=meta['phash'],
                width=meta['width'],
                height=meta['height'],
                has_exif=1 if date_result.source == 'exif' else 0,
                date_value=date_result.date.isoformat() if date_result.date else None,
                date_source=date_result.source,
            )

            if progress_cb:
                progress_cb(i, len(files), filepath)

    return stats
