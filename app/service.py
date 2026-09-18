"""Служба: периодически читает MAX и выполняет одобренные действия.

Отдельно от веб-интерфейса намеренно. Интерфейс нужен человеку и может быть
закрыт, а служба должна работать сама. Запускается либо как отдельный процесс,
либо фоновым потоком внутри веб-приложения (`--with-web`).

Устойчивость важнее скорости: любая ошибка одного круга логируется и не
останавливает службу, потому что упавший бот тихо перестаёт обрабатывать
заявки, и заметят это не сразу.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import time

from app.config import Settings, settings as default_settings
from app.core.classify import Classifier
from app.core.kb import KnowledgeBase
from app.core.llm import LocalLlm
from app.core.pipeline import Pipeline
from app.executors.runner import ActionRunner
from app.store import Store
from app.transports.base import NotAuthenticated, TransportError

log = logging.getLogger("dezhurny.service")


class DispatcherService:
    """Бесконечный цикл опроса с мягкой остановкой."""

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self.store = Store(self.cfg.database_url.replace("sqlite:///", ""))
        from app.web.app import build_transport  # ленивый импорт: избегаем цикла

        self.transport = build_transport(self.cfg)
        llm = LocalLlm(self.cfg.llm_base_url, self.cfg.llm_model,
                       timeout=self.cfg.llm_timeout, enabled=self.cfg.llm_enabled)
        self.pipeline = Pipeline(self.store, self.transport, Classifier(llm),
                                 KnowledgeBase(self.cfg.kb_dir), self.cfg)
        self.runner = ActionRunner(self.store, self.transport, self.cfg)
        self._stop = threading.Event()

    # ------------------------------------------------------------------ круг
    def tick(self) -> dict:
        """Один круг: прочитать новое, выполнить одобренное.

        В отчёте внешние действия и внутренние записи в журнал считаются
        отдельно. Иначе в логе видно «выполнено 2» при включённом
        предохранителе, и можно решить, что бот всё-таки кому-то написал.
        """
        summary = self.pipeline.poll_once()
        results = self.runner.run_pending()
        kinds = {int(row["id"]): row["kind"]
                 for row in self.store._all("SELECT id, kind FROM actions")}
        external = [(aid, r) for aid, r in results if kinds.get(aid) != "note"]
        summary["sent_to_max"] = sum(1 for _, r in external if r.ok and not r.dry_run)
        summary["held_by_guard"] = sum(1 for _, r in external if r.dry_run)
        summary["failed"] = sum(1 for _, r in external if not r.ok)
        summary["notes"] = len(results) - len(external)
        return summary

    def run_forever(self) -> None:
        self.transport.start()
        log.info(
            "служба запущена: транспорт %s, режим %s, предохранитель %s, "
            "интервал %s с",
            type(self.transport).__name__, self.cfg.dispatcher_mode.value,
            "включён" if self.cfg.safe_mode else "ВЫКЛЮЧЕН",
            self.cfg.max_poll_interval,
        )
        if self.cfg.safe_mode:
            log.info("наружу ничего не отправляется, пока SAFE_MODE=true")

        while not self._stop.is_set():
            try:
                summary = self.tick()
                if summary["new_messages"] or summary["sent_to_max"]:
                    log.info("круг: %s", summary)
            except NotAuthenticated:
                log.error("нет сессии MAX — нужен вход человеком, ждём минуту")
                self._stop.wait(60)
                continue
            except TransportError as e:
                log.warning("транспорт подвёл (%s) — повторим на следующем круге", e)
            except Exception:  # noqa: BLE001 — служба не должна умирать молча
                log.exception("непредвиденная ошибка в круге")
            self._stop.wait(self.cfg.max_poll_interval)

        self.transport.stop()
        log.info("служба остановлена")

    def stop(self) -> None:
        self._stop.set()

    # -------------------------------------------------------------- в потоке
    def start_background(self) -> threading.Thread:
        """Запускает службу фоновым потоком — для режима «всё в одном процессе»."""
        thread = threading.Thread(target=self.run_forever, name="dezhurny",
                                  daemon=True)
        thread.start()
        return thread


def main() -> None:
    parser = argparse.ArgumentParser(description="Дежурный — служба опроса MAX")
    parser.add_argument("--once", action="store_true",
                        help="сделать один круг и выйти (удобно для проверки)")
    parser.add_argument("--with-web", action="store_true",
                        help="поднять заодно веб-интерфейс на :8000")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    service = DispatcherService()
    if args.once:
        service.transport.start()
        print(service.tick())
        service.transport.stop()
        return

    signal.signal(signal.SIGINT, lambda *_: service.stop())
    signal.signal(signal.SIGTERM, lambda *_: service.stop())

    if args.with_web:
        from app.web.app import create_app

        service.start_background()
        create_app(service.cfg, store=service.store,
                   transport=service.transport).run(host="127.0.0.1", port=8000)
        service.stop()
        return

    service.run_forever()


if __name__ == "__main__":
    main()
