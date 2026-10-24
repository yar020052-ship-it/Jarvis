"""Уведомление об обновлении в UI (фоновая проверка + баннер)."""

import threading
import time
from typing import Callable, Optional


try:
    from jarvis.core.updater import check_for_update, is_newer, APP_VERSION
except Exception:
    try:
        from ..core.updater import check_for_update, is_newer, APP_VERSION  # type: ignore
    except Exception:
        raise


MANIFEST_URL_DEFAULT = "https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json"


class UpdateChecker:
    def __init__(self, manifest_url: str = MANIFEST_URL_DEFAULT, on_available: Optional[Callable] = None, log: Callable[[str], None] = lambda x: None):
        self.manifest_url = manifest_url
        self.on_available = on_available
        self.log = log
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._info = None
        self._checked = 0.0

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            try:
                info = check_for_update(self.manifest_url, self.log)
                if info and is_newer(info.version, APP_VERSION):
                    self._info = info
                    if self.on_available:
                        try:
                            self.on_available(info)
                        except Exception:
                            pass
                    # после показа уведомления можно реже проверять? но просто ждём
                    # не блокируем
            except Exception as e:
                self.log(f"Checker error: {e}")
            self._checked = time.time()
            # проверка раз в 6 часов, но при старте сразу? делаем с паузой 30с после старта
            for _ in range(60 * 60 * 6):  # 6ч
                if self._stop.is_set():
                    break
                time.sleep(1)

    def get_info(self):
        return self._info


def start_background_check(manifest_url: str = MANIFEST_URL_DEFAULT, on_available: Optional[Callable] = None, log: Callable[[str], None] = lambda x: None):
    c = UpdateChecker(manifest_url, on_available, log)
    c.start()
    return c
