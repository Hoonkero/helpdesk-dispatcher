"""Веб-интерфейс оператора: сводка, заявки, очередь на одобрение.

Интерфейс — не украшение, а предохранитель. В режиме shadow бот не делает в
MAX ничего, пока человек не нажмёт «Одобрить», и каждое предложенное действие
показывается вместе с текстом, который уйдёт человеку, и с объяснением, зачем
бот это делает.

Оформление — дизайн-система SoftPanel: только классы `sp-*` и токены.
"""
from __future__ import annotations

import logging

from flask import Flask, abort, flash, redirect, render_template, request, url_for

from app.config import Settings, settings as default_settings
from app.core.classify import Classifier
from app.core.kb import KnowledgeBase
from app.core.llm import LocalLlm
from app.core.pipeline import Pipeline
from app.domain import ActionStatus, TicketStatus
from app.executors.runner import ActionRunner
from app.store import Store, row_to_dict
from app.transports.base import MaxTransport, TransportError
from app.transports.fake import FakeMaxTransport
from app.web import labels

log = logging.getLogger(__name__)

#: Фильтры на странице заявок: значение статуса и подпись.
TICKET_FILTERS = (
    ("new", "Новые"),
    ("clarifying", "Уточняем"),
    ("in_progress", "В работе"),
    ("waiting_third_party", "У коллег"),
    ("resolved", "Решены"),
)


def build_transport(cfg: Settings) -> MaxTransport:
    """Создаёт транспорт по настройкам. Заглушка — безопасный вариант по умолчанию."""
    if cfg.max_transport == "playwright":
        try:
            from app.transports.playwright_max import PlaywrightMaxTransport
        except ImportError as e:
            log.warning("транспорт playwright недоступен (%s) — работаем на заглушке", e)
            return FakeMaxTransport()
        return PlaywrightMaxTransport(cfg.max_web_url, cfg.max_profile_dir)
    if cfg.max_transport == "sandbox":
        try:
            from app.transports.playwright_max import PlaywrightMaxTransport
        except ImportError:
            return FakeMaxTransport()
        return PlaywrightMaxTransport("http://127.0.0.1:8765", cfg.max_profile_dir)
    return FakeMaxTransport()


def create_app(cfg: Settings | None = None, *, store: Store | None = None,
               transport: MaxTransport | None = None) -> Flask:
    """Собирает приложение. Параметры нужны тестам, чтобы подставить свои части."""
    cfg = cfg or default_settings
    app = Flask(__name__)
    app.secret_key = "dezhurny-local"  # интерфейс локальный, сессия нужна только для flash

    app.state_store = store or Store(cfg.database_url.replace("sqlite:///", ""))
    app.state_transport = transport or build_transport(cfg)
    app.state_transport.start()
    app.state_kb = KnowledgeBase(cfg.kb_dir)
    llm = LocalLlm(cfg.llm_base_url, cfg.llm_model, timeout=cfg.llm_timeout,
                   enabled=cfg.llm_enabled)
    app.state_pipeline = Pipeline(
        app.state_store, app.state_transport, Classifier(llm), app.state_kb, cfg)
    app.state_runner = ActionRunner(app.state_store, app.state_transport, cfg)
    app.state_cfg = cfg

    # -------------------------------------------------------- общий контекст
    app.jinja_env.filters["human_time"] = labels.human_time

    @app.context_processor
    def inject() -> dict:
        return {
            "settings": cfg,
            "stats": app.state_store.stats(),
            "transport_name": type(app.state_transport).__name__,
            "status_label": lambda v: labels.STATUS_LABELS.get(str(v), str(v)),
            "status_variant": lambda v: labels.STATUS_VARIANTS.get(str(v), "brand"),
            "category_label": lambda v: labels.CATEGORY_LABELS.get(_val(v), _val(v)),
            "resolution_label": lambda v: labels.RESOLUTION_LABELS.get(_val(v), _val(v)),
            "action_label": lambda v: labels.ACTION_LABELS.get(str(v), str(v)),
            "action_status_label": lambda v: labels.ACTION_STATUS_LABELS.get(str(v), str(v)),
            "action_variant": lambda v: labels.ACTION_STATUS_VARIANTS.get(str(v), "brand"),
            "entity_label": lambda v: labels.ENTITY_LABELS.get(str(v), str(v)),
        }

    # ------------------------------------------------------------- страницы
    @app.get("/")
    def index():
        rows = [row_to_dict(r) for r in app.state_store.tickets(limit=10)]
        return render_template("index.html", recent=rows)

    @app.get("/tickets")
    def tickets():
        status = request.args.get("status") or None
        rows = [row_to_dict(r) for r in app.state_store.tickets(status=status, limit=200)]
        counts = {
            value: len(app.state_store.tickets(status=value, limit=500))
            for value, _ in TICKET_FILTERS
        }
        filters = [(v, label, counts[v]) for v, label in TICKET_FILTERS]
        return render_template("tickets.html", rows=rows, current=status, filters=filters)

    @app.get("/ticket/<int:ticket_id>")
    def ticket_detail(ticket_id: int):
        row = app.state_store.ticket(ticket_id)
        if row is None:
            abort(404)
        return render_template(
            "ticket.html",
            ticket=row_to_dict(row),
            ticket_actions=[row_to_dict(a) for a in app.state_store.actions_for(ticket_id)],
        )

    @app.get("/actions")
    def actions():
        return render_template(
            "actions.html",
            rows=[row_to_dict(a) for a in app.state_store.pending_actions()],
        )

    # ------------------------------------------------------------- действия
    @app.post("/action/<int:action_id>/approve")
    def approve_action(action_id: int):
        if app.state_store.action(action_id) is None:
            abort(404)
        app.state_store.set_action_status(action_id, ActionStatus.approved)
        result = app.state_runner.execute(action_id)
        if result.dry_run:
            flash(f"Одобрено, но не выполнено: {result.output}", "success")
        elif result.ok:
            flash(f"Выполнено: {result.output}", "success")
        else:
            flash(f"Не получилось: {result.error}", "danger")
        return redirect(request.referrer or url_for("actions"))

    @app.post("/action/<int:action_id>/reject")
    def reject_action(action_id: int):
        row = app.state_store.action(action_id)
        if row is None:
            abort(404)
        app.state_store.set_action_status(
            action_id, ActionStatus.rejected, "отклонено оператором")
        app.state_store.log(int(row["ticket_id"]), "Оператор отклонил предложенное действие")
        flash("Действие отклонено", "success")
        return redirect(request.referrer or url_for("actions"))

    @app.post("/poll")
    def do_poll():
        try:
            summary = app.state_pipeline.poll_once()
        except TransportError as e:
            flash(f"Не удалось прочитать MAX: {e}", "danger")
            return redirect(url_for("index"))
        flash(
            f"Прочитано чатов: {summary['chats']}, новых сообщений: "
            f"{summary['new_messages']}, заявок: {summary['tickets']}, "
            f"запланировано действий: {summary['actions']}. "
            f"Приглушённых пропущено: {summary['skipped_muted']}.",
            "success",
        )
        return redirect(url_for("index"))

    @app.post("/run-actions")
    def do_run_actions():
        results = app.state_runner.run_pending()
        done = sum(1 for _, r in results if r.ok and not r.dry_run)
        dry = sum(1 for _, r in results if r.dry_run)
        bad = sum(1 for _, r in results if not r.ok)
        flash(f"Выполнено: {done}, пропущено предохранителем: {dry}, ошибок: {bad}",
              "danger" if bad else "success")
        return redirect(request.referrer or url_for("actions"))

    @app.post("/ticket/<int:ticket_id>/close")
    def close_ticket(ticket_id: int):
        if app.state_store.ticket(ticket_id) is None:
            abort(404)
        app.state_store.set_status(ticket_id, TicketStatus.closed_by_human)
        app.state_store.log(ticket_id, "Оператор закрыл заявку вручную")
        flash("Заявка закрыта", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    return app


def _val(value: object) -> str:
    """Значение перечисления или строка — в строку."""
    return getattr(value, "value", None) or str(value)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_app().run(host="127.0.0.1", port=8000, debug=False)
