"""
undo.py — відкат пачки операцій (сортування чи перейменування) за batch_id.

Читає таблицю operations у зворотньому порядку і повертає кожен
файл/папку на початковий шлях. Для 'copy'/'copy_verify_delete'
відкат означає: якщо оригінал ще існує — просто видалити копію;
якщо оригінал видалено (safe-copy режим) — скопіювати назад.
"""

from pathlib import Path
from . import db


def undo_batch(index_db_path: Path, batch_id: str) -> dict:
    stats = {'reverted': 0, 'errors': 0}

    with db.open_db(index_db_path) as conn:
        operations = db.get_batch_operations(conn, batch_id)

        for op in operations:
            src, dst = Path(op['src_path']), Path(op['dst_path'])
            try:
                if op['op_type'] == 'rename':
                    if dst.exists():
                        dst.rename(src)

                elif op['op_type'] == 'move':
                    if dst.exists():
                        src.parent.mkdir(parents=True, exist_ok=True)
                        dst.rename(src)

                elif op['op_type'] == 'copy':
                    if dst.exists():
                        dst.unlink()

                elif op['op_type'] == 'copy_verify_delete':
                    if dst.exists() and not src.exists():
                        src.parent.mkdir(parents=True, exist_ok=True)
                        import shutil
                        shutil.copy2(dst, src)
                        dst.unlink()

                stats['reverted'] += 1
            except Exception:
                stats['errors'] += 1

    return stats
