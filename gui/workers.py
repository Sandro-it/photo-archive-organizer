"""
workers.py — QThread-обгортки над функціями з core/, щоб довгі
операції (сканування тисяч файлів, копіювання) не блокували GUI.
"""

from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from core.scanner import collect_files
from core.sorter import plan_sort, execute_sort, SortConfig, PlannedMove
from core.renamer import plan_rename, execute_rename, RenameRule
from core.duplicates import find_all_duplicates, DEFAULT_MAX_HAMMING_DISTANCE


class PlanSortWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_plan = pyqtSignal(list)   # list[PlannedMove]
    error = pyqtSignal(str)

    def __init__(self, cfg: SortConfig):
        super().__init__()
        self.cfg = cfg

    def run(self):
        try:
            files = collect_files(self.cfg.sources, recursive=self.cfg.recursive)
            plan = plan_sort(self.cfg, files)
            self.finished_plan.emit(plan)
        except Exception as e:
            self.error.emit(str(e))


class ExecuteSortWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_stats = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, cfg: SortConfig, plan: list[PlannedMove], index_db_path: Path, batch_id: str):
        super().__init__()
        self.cfg = cfg
        self.plan = plan
        self.index_db_path = index_db_path
        self.batch_id = batch_id

    def run(self):
        try:
            def cb(i, total, item):
                self.progress.emit(i, total)

            stats = execute_sort(self.cfg, self.plan, self.index_db_path, self.batch_id, progress_cb=cb)
            self.finished_stats.emit(stats)
        except Exception as e:
            self.error.emit(str(e))


class PlanRenameWorker(QThread):
    finished_plan = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, rule: RenameRule, folders: list[Path]):
        super().__init__()
        self.rule = rule
        self.folders = folders

    def run(self):
        try:
            plan = plan_rename(self.rule, self.folders)
            self.finished_plan.emit(plan)
        except Exception as e:
            self.error.emit(str(e))


class FindDuplicatesWorker(QThread):
    """Порівняння sha256/phash по вже побудованому індексу — може бути
    повільним на великих архівах (O(n^2) для similar), тому в окремому потоці."""

    finished_groups = pyqtSignal(dict)   # {'exact': [DuplicateGroup], 'similar': [DuplicateGroup]}
    error = pyqtSignal(str)

    def __init__(self, index_db_path: Path, max_distance: int = DEFAULT_MAX_HAMMING_DISTANCE):
        super().__init__()
        self.index_db_path = index_db_path
        self.max_distance = max_distance

    def run(self):
        try:
            groups = find_all_duplicates(self.index_db_path, max_distance=self.max_distance)
            self.finished_groups.emit(groups)
        except Exception as e:
            self.error.emit(str(e))
