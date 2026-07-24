"""
Database Handler for Matlib - Saves Data as json to disk and ensures only one active connection
"""

import json
import os
from typing import Self


class DatabaseConnector:
    """
    Database Handler for Matlib - saves data as json to disk with one
    active connection PER DATABASE FILE. Historically a plain singleton
    hardcoded to library.json; the v2 COP section runs a second,
    fully independent library over cops.json, so instances are now keyed
    by filename (same instance returned for the same filename - all
    existing callers pass nothing and keep sharing the library.json
    connection exactly as before).
    """

    _instances: dict = {}

    def __new__(cls, filename: str = "library.json") -> Self:
        inst = cls._instances.get(filename)
        if inst is None:
            inst = super().__new__(cls)
            inst._filename = filename
            inst._data = {}
            inst._path = ""
            cls._instances[filename] = inst
        return inst

    def load(self, path: str) -> dict:
        """
        Loads the Database from disk as json. Secondary databases
        (anything that isn't library.json) are seeded as an empty
        library on first use instead of failing - library.json itself
        keeps the old behavior, since a missing PRIMARY database is a
        real error the caller must surface, not silently paper over.
        """
        self._path = path
        if not self._data:
            full = self._path + self._filename
            if not os.path.exists(full) and self._filename != "library.json":
                # "_All", not "All": the leading underscore is the
                # library's long-standing sort trick - it sorts before
                # any letter so the pseudo-category stays pinned on top,
                # and Categories.data() strips it for display.
                self._data = {"categories": ["_All"], "tags": [], "assets": []}
                self.save()
            else:
                with open(full, encoding="utf_8") as lib_json:
                    self._data = json.load(lib_json)
                if self._filename != "library.json":
                    self._normalize_all_category()
        return self._data

    def _normalize_all_category(self) -> None:
        """One-time repair for secondary databases seeded before the
        "_All" convention was honored there: a plain "All" entry sorted
        alphabetically among real categories instead of pinning to the
        top. Rewrites it to "_All" (and guarantees the entry exists)."""
        cats = self._data.get("categories")
        if not isinstance(cats, list):
            return
        changed = False
        if "All" in cats:
            cats[:] = [c for c in cats if c != "All"]
            changed = True
        if "_All" not in cats:
            cats.insert(0, "_All")
            changed = True
        if changed:
            self.save()

    def set(self, assets: dict) -> None:
        """Set Data without saving"""
        if "categories" in assets.keys():
            self._data["categories"] = assets["categories"]
        if "tags" in assets.keys():
            self._data["tags"] = assets["tags"]
        if "assets" in assets.keys():
            self._data["assets"] = assets["assets"]

    def save(self) -> None:
        """Save Data to Disk"""
        if not self._data:
            return
        with open(self._path + self._filename, "w", encoding="utf-8") as lib_json:
            json.dump(self._data, lib_json, indent=4)

    def reload_with_path(self, path: str) -> dict:
        self._data = None
        return self.load(path)
