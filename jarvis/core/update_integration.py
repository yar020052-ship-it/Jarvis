import os
import threading
from typing import Callable

try:
    from core.paths import get_cache_dir
    from jarvis.core.update_source import check_for_update, is_newer, download_update, _detect_platform, APP_VERSION
except Exception:
    try:
        from ..core.update_source import check_for_update, is_newer, download_update, _detect_platform, APP_VERSION  # type: ignore
        from core.paths import get_cache_dir  # type: ignore
    except Exception:
        raise

MANIFEST_URL_DEFAULT = os.environ.get("JARVIS_UPDATE_MANIFEST") or os.environ.get("JARVIS_UPDATE_URL") or "https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json"


class UpdateManager:
    def __init__(self, ui=None, manifest_url: str = MANIFEST_URL_DEFAULT, log: Callable[[str], None] = lambda x: None):
        self.ui = ui
        self.manifest_url = manifest_url
        self.log = log
        self._checker_thread = None
        self._info = None

    def check_now_background(self):
        t = threading.Thread(target=self._check_now, daemon=True)
        t.start()
        return t

    def _check_now(self):
        try:
            # Плейсхолдер без настроенного репозитория — проверка отключена.
            if not self.manifest_url or "YOUR-REPO" in self.manifest_url:
                return
            info = check_for_update(self.manifest_url, self.log)
            if not info:
                return
            if is_newer(info.version, APP_VERSION):
                self._info = info
                self._notify_ui(info)
        except Exception as e:
            self.log(f"Update check error: {e}")

    def _notify_ui(self, info):
        if not self.ui:
            return
        title = f"Доступно обновление Jarvis v{info.version}"
        text = info.changelog_ru or info.changelog or "Нажмите Обновить, чтобы установить"
        actions = [
            {"label": "Обновить", "id": "update_now"},
            {"label": "Позже", "id": "update_later"},
            {"label": "Что нового", "id": "update_changelog"},
        ]
        try:
            if hasattr(self.ui, "notify"):
                try:
                    self.ui.notify(title, text[:400], level="info")
                except Exception:
                    pass
            try:
                if hasattr(self.ui, "_win") and hasattr(self.ui._win, "_toast_action_sig"):
                    import json

                    self.ui._win._toast_action_sig.emit(title[:120], text[:400], "info", json.dumps(actions, ensure_ascii=False))
            except Exception:
                pass
        except Exception:
            pass
        self.log(f"Update available: v{info.version}")

    def perform_update(self):
        if not self._info:
            return False
        try:
            plat = _detect_platform()
            d = get_cache_dir() / "updates"
            d.mkdir(parents=True, exist_ok=True)
            z = d / f"Jarvis-v{self._info.version}-{plat}.zip"
            if download_update(self._info, plat, z, self.log):
                self.log(f"Update downloaded: {z}")
                self._launch_updater(z)
                return True
        except Exception as e:
            self.log(f"Update download error: {e}")
        return False

    def _launch_updater(self, zip_path):
        try:
            import sys
            import subprocess
            from pathlib import Path

            app_dir_p = Path(sys.argv[0]).resolve().parent
            updater_py = app_dir_p / "scripts" / "update.py"
            # Список аргументов на ВСЕХ платформах — как в не-Windows-ветке ниже.
            # Раньше Windows шла через shell=True с f-строкой: лишний cmd.exe в
            # цепочке, и путь, содержащий & или ^, ломал запуск обновления.
            subprocess.Popen(
                [sys.executable, str(updater_py), "--app-dir", str(app_dir_p), "--zip", str(zip_path)]
            )
            try:
                import os as _os

                _os._exit(0)
            except Exception:
                sys.exit(0)
        except Exception as e:
            self.log(f"Launch updater error: {e}")
