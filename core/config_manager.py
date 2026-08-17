"""
core/config_manager.py — 設定ホットリロード管理モジュール

ファイルの最終更新日時（mtime）を監視し、ファイル変更時に自動で設定を再読み込みする。
"""
import os
import yaml
from typing import Optional


class ConfigManager:
    def __init__(self, config_path: str):
        self.config_path = os.path.abspath(config_path)
        self._last_mtime: Optional[float] = None
        self._config: dict = {}
        self.reload()

    def reload(self) -> dict:
        """設定ファイルを強制再読み込みする。"""
        if os.path.exists(self.config_path):
            self._last_mtime = os.path.getmtime(self.config_path)
            with open(self.config_path, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}
        return self._config

    def get_config(self) -> dict:
        """
        設定を取得する。
        ファイルが変更されていれば自動で再読み込みを行う（ホットリロード）。
        """
        if os.path.exists(self.config_path):
            current_mtime = os.path.getmtime(self.config_path)
            if self._last_mtime is None or current_mtime > self._last_mtime:
                self.reload()
        return self._config
