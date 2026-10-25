"""Shared research-data contracts. Importing this package performs no acquisition."""

from .study import Study, find_root, load_study

__all__ = ["Study", "find_root", "load_study"]
