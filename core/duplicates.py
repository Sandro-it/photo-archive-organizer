"""
duplicates.py — пошук дублікатів у вже побудованому SQLite-індексі.

Працює виключно з даними, які scanner.py уже поклав у files
(sha256, phash, width/height, has_exif) — повторного сканування
диска тут немає, усе через SQL-запити й порівняння в пам'яті.

Два типи груп:
  - 'exact'   — однаковий sha256 (побітово ідентичні файли).
  - 'similar' — різний sha256, але phash відрізняється не більше
                ніж на max_distance бітів (візуально схожі копії:
                пересхоплені, конвертовані, з іншим стиском).

Для кожного файлу в групі рахується "бал якості", і файл з
найвищим балом позначається рекомендованим оригіналом —
рішення, що видаляти, лишається за користувачем (GUI), сам
модуль нічого не видаляє.
"""

import itertools
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db


DEFAULT_MAX_HAMMING_DISTANCE = 5

# Патерни в імені файлу, які натякають на скріншот/пересилку месенджером,
# а не на оригінал з камери — такі копії зазвичай гіршої якості.
SUSPICIOUS_NAME_PATTERNS = [
    re.compile(r'screenshot', re.IGNORECASE),
    re.compile(r'whatsapp', re.IGNORECASE),
]


@dataclass
class FileScore:
    path: Path
    size: int
    width: int | None
    height: int | None
    has_exif: bool
    score: int
    reasons: list[str]


@dataclass
class DuplicateGroup:
    kind: str   # 'exact' | 'similar'
    key: str    # sha256 (exact) або phash одного з файлів групи (similar)
    files: list[FileScore]   # відсортовано за score спадно

    @property
    def recommended(self) -> FileScore:
        """Файл з найвищим балом — рекомендований оригінал для збереження."""
        return self.files[0]


def _score_file(row: sqlite3.Row) -> tuple[int, list[str]]:
    """Рахує бал якості файлу: більше EXIF/роздільності/розміру — краще,
    підозріле ім'я (скріншот, месенджер) — штраф."""
    score = 0
    reasons = []

    if row['has_exif']:
        score += 20
        reasons.append("має EXIF")

    width, height = row['width'], row['height']
    if width and height:
        megapixels = (width * height) / 1_000_000
        score += round(megapixels * 5)
        reasons.append(f"роздільність {width}x{height}")

    size = row['size'] or 0
    score += min(size // (200 * 1024), 10)
    reasons.append(f"розмір {size} байт")

    filename = Path(row['path']).name
    for pattern in SUSPICIOUS_NAME_PATTERNS:
        if pattern.search(filename):
            score -= 25
            reasons.append(f"підозріле ім'я файлу (схоже на «{pattern.pattern}»)")
            break

    return score, reasons


def _row_to_filescore(row: sqlite3.Row) -> FileScore:
    score, reasons = _score_file(row)
    return FileScore(
        path=Path(row['path']),
        size=row['size'] or 0,
        width=row['width'],
        height=row['height'],
        has_exif=bool(row['has_exif']),
        score=score,
        reasons=reasons,
    )


def find_exact_duplicates(conn: sqlite3.Connection) -> list[DuplicateGroup]:
    """Групи файлів з однаковим sha256 (побітово ідентичні)."""
    rows = conn.execute(
        """
        SELECT * FROM files
        WHERE sha256 IN (
            SELECT sha256 FROM files
            WHERE sha256 IS NOT NULL
            GROUP BY sha256
            HAVING COUNT(*) > 1
        )
        ORDER BY sha256
        """
    ).fetchall()

    groups = []
    for sha256, group_rows in itertools.groupby(rows, key=lambda r: r['sha256']):
        files = [_row_to_filescore(r) for r in group_rows]
        files.sort(key=lambda f: f.score, reverse=True)
        groups.append(DuplicateGroup(kind='exact', key=sha256, files=files))
    return groups


def _hamming_distance(hash_a: str, hash_b: str) -> int:
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count('1')


class _UnionFind:
    """Мінімальний union-find для групування файлів у кластери схожості."""

    def __init__(self, size: int):
        self._parent = list(range(size))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b


def find_similar_duplicates(
    conn: sqlite3.Connection,
    max_distance: int = DEFAULT_MAX_HAMMING_DISTANCE,
    exclude_sha256: set[str] | None = None,
) -> list[DuplicateGroup]:
    """Групи візуально схожих файлів (phash, відстань Хеммінга <= max_distance).

    exclude_sha256 дозволяє виключити файли, які вже потрапили в точні
    дублікати (find_exact_duplicates), щоб та сама пара не показувалась
    двічі — і як 'exact', і як 'similar'.

    Порівняння попарне (O(n^2)) — прийнятно для особистого фотоархіву
    (тисячі файлів), але не розраховане на мільйони записів.
    """
    exclude_sha256 = exclude_sha256 or set()
    rows = [
        r for r in conn.execute("SELECT * FROM files WHERE phash IS NOT NULL").fetchall()
        if r['sha256'] not in exclude_sha256
    ]

    uf = _UnionFind(len(rows))
    for i, j in itertools.combinations(range(len(rows)), 2):
        if _hamming_distance(rows[i]['phash'], rows[j]['phash']) <= max_distance:
            uf.union(i, j)

    clusters: dict[int, list[sqlite3.Row]] = {}
    for i, row in enumerate(rows):
        clusters.setdefault(uf.find(i), []).append(row)

    groups = []
    for cluster_rows in clusters.values():
        if len(cluster_rows) < 2:
            continue
        files = [_row_to_filescore(r) for r in cluster_rows]
        files.sort(key=lambda f: f.score, reverse=True)
        groups.append(DuplicateGroup(kind='similar', key=cluster_rows[0]['phash'], files=files))
    return groups


def find_all_duplicates(
    index_db_path: Path,
    max_distance: int = DEFAULT_MAX_HAMMING_DISTANCE,
) -> dict[str, list[DuplicateGroup]]:
    """Зручна точка входу для GUI: відкриває індекс і повертає обидва типи груп."""
    with db.open_db(index_db_path) as conn:
        exact_groups = find_exact_duplicates(conn)
        exact_shas = {g.key for g in exact_groups}
        similar_groups = find_similar_duplicates(conn, max_distance=max_distance, exclude_sha256=exact_shas)

    return {'exact': exact_groups, 'similar': similar_groups}
