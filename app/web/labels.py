"""Человеческие подписи для интерфейса.

Правило дизайн-системы: статус всегда подписан словом, не только цветом.
Поэтому у каждого технического значения здесь есть русское название и вариант
бейджа.
"""
from __future__ import annotations

from datetime import datetime

STATUS_LABELS = {
    "new": "Новая",
    "acknowledged": "Принята",
    "clarifying": "Уточняем",
    "in_progress": "В работе",
    "waiting_user_confirm": "Ждём подтверждения",
    "waiting_third_party": "Передана коллеге",
    "resolved": "Решена",
    "closed_by_human": "Закрыта вручную",
    "cancelled": "Отменена",
}

STATUS_VARIANTS = {
    "new": "orange",
    "acknowledged": "brand",
    "clarifying": "warning",
    "in_progress": "brand",
    "waiting_user_confirm": "warning",
    "waiting_third_party": "warning",
    "resolved": "success",
    "closed_by_human": "success",
    "cancelled": "danger",
}

CATEGORY_LABELS = {
    "access_blocked": "Нет доступа или учётка заблокирована",
    "password_reset": "Сброс пароля",
    "printer": "Печать и принтеры",
    "software_install": "Установка или обновление программы",
    "cloud_access": "Доступ к сервису",
    "hardware": "Техника",
    "network": "Сеть и интернет",
    "howto": "Вопрос «как сделать»",
    "info_broadcast": "Объявление",
    "smalltalk": "Благодарность",
    "unknown": "Не определено",
}

RESOLUTION_LABELS = {
    "auto_answer": "Ответ из базы знаний",
    "need_clarify": "Уточнить в личке",
    "remote_script": "Удалённо через Aspia",
    "escalate_email": "Передать ответственному",
    "needs_human": "Нужен человек на месте",
    "ignore": "Реагировать не нужно",
}

ACTION_LABELS = {
    "react_seen": "Реакция «увидел»",
    "react_done": "Реакция «закрыто»",
    "send_dm": "Сообщение в личку",
    "aspia_script": "Скрипт через Aspia",
    "forward_email": "Передать ответственному",
    "note": "Запись в журнал",
}

ACTION_STATUS_LABELS = {
    "proposed": "Ждёт решения",
    "approved": "Одобрено",
    "done": "Выполнено",
    "failed": "Ошибка",
    "rejected": "Отклонено",
    "skipped": "Пропущено",
}

ACTION_STATUS_VARIANTS = {
    "proposed": "orange",
    "approved": "brand",
    "done": "success",
    "failed": "danger",
    "rejected": "warning",
    "skipped": "warning",
}

ENTITY_LABELS = {
    "email": "Почта",
    "room": "Кабинет",
    "aspia_code": "Код Aspia",
    "phone": "Телефон",
}


def human_time(value: str | datetime | None) -> str:
    """Дата в виде «17 сентября, 23:05». Числа — табличными цифрами (класс sp-num)."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря")
    return f"{value.day} {months[value.month - 1]}, {value:%H:%M}"
