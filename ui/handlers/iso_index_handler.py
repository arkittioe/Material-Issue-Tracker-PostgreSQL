from PyQt6.QtCore import *
from PyQt6.QtCore import pyqtSignal, QObject
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from data_manager_facade import DataManagerFacade as DataManager
from watchdog.events import FileSystemEventHandler
import os
import sys

class IsoIndexEventHandler(QObject, FileSystemEventHandler):  # 👈 **ORDER SWAPPED HERE**
    """
    This class reacts to file system changes (create, delete, modify)
    and calls the appropriate DataManager functions to update the database.
    """
    status_updated = pyqtSignal(str, str)
    progress_updated = pyqtSignal(int, str)

    def __init__(self, dm: DataManager):
        # The super().__init__() call now correctly initializes the QObject first.
        super().__init__()
        # We no longer need to call FileSystemEventHandler.__init__() separately.

        self.dm = dm
        self.SUPPORTED_EXTENSIONS = {".pdf", ".dwg"}

    def _is_supported(self, path):
        return os.path.splitext(path)[1].lower() in self.SUPPORTED_EXTENSIONS

    def on_created(self, event):
        if not event.is_directory and self._is_supported(event.src_path):
            print(f"File created: {event.src_path}")
            self.dm.upsert_iso_index_entry(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_supported(event.src_path):
            print(f"File deleted: {event.src_path}")
            self.dm.remove_iso_index_entry(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._is_supported(event.src_path):
            print(f"File modified: {event.src_path}")
            self.dm.upsert_iso_index_entry(event.src_path)

    def on_moved(self, event):
        if not event.is_directory and self._is_supported(event.src_path):
            print(f"File moved: from {event.src_path} to {event.dest_path}")
            self.dm.remove_iso_index_entry(event.src_path)
            if self._is_supported(event.dest_path):
                self.dm.upsert_iso_index_entry(event.dest_path)
