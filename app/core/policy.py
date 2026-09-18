"""Политика: что бот собирается делать с обращением.

Здесь живут правила поведения, заданные заказчиком, и они выражены кодом, а не
комментариями:

1. В чат заявок бот не пишет никогда — только реакции: 👌 «увидел», ✔️ «закрыто».
2. Уточнения и ответы — только в личных сообщениях автору обращения.
3. Объявления и благодарности не получают даже реакции: это не заявки.
4. Если для решения не хватает данных (почты, кода доступа, кабинета) — сначала
   уточняем в личке, и только потом действуем.
5. Заявку, требующую присутствия, бот помечает принятой и оставляет человеку.

Политика ничего не выполняет. Она возвращает план — список PlannedAction,
который дальше проходит через одобрение оператора и исполнителей.
"""
from __future__ import annotations

from app.core.kb import Article, KnowledgeBase
from app.core.rules import RuleVerdict, missing_entities
from app.domain import (
    ActionKind,
    Category,
    Decision,
    IncomingMessage,
    PlannedAction,
    Resolution,
)

#: Категории, на которые бот не реагирует вообще.
SILENT_CATEGORIES = frozenset({Category.info_broadcast, Category.smalltalk})

#: Человеческие названия недостающих данных — для текста уточнения.
ENTITY_NAMES = {
    "email": "вашу рабочую почту",
    "room": "номер кабинета",
    "aspia_code": "код доступа из приложения Aspia",
}

WAIT_TEMPLATE = (
    "Здравствуйте! Заявку принял и передал ответственному — "
    "{email} разблокируют в рабочем порядке. "
    "Подождите, пожалуйста, я напишу здесь, как только доступ вернут. "
    "Повторные попытки входа лучше не делать, они продлевают блокировку."
)

FORWARD_TEMPLATE = (
    "Коллеги, обращение от {requester}: {summary}\n"
    "Почта для разблокировки: {email}\n"
    "Пользователю написал, что заявка принята и нужно подождать."
)

CONFIRM_TEMPLATE = (
    "Готово — {done}. Проверьте, пожалуйста, и напишите, всё ли теперь работает."
)


def _clarify_text(missing: list[str], article: Article | None) -> str:
    """Текст уточняющего вопроса: сначала суть, потом чего не хватает."""
    need = " и ".join(ENTITY_NAMES.get(m, m) for m in missing)
    lead = "Здравствуйте! Вижу вашу заявку."
    if article and article.clarifying_questions:
        return f"{lead} Подскажите, пожалуйста, {need} — это нужно, чтобы я начал."
    if need:
        return f"{lead} Подскажите, пожалуйста, {need}."
    return f"{lead} Опишите чуть подробнее, что происходит, — и я подключусь."


def build_decision(
    msg: IncomingMessage,
    verdict: RuleVerdict,
    kb: KnowledgeBase,
    *,
    is_request_channel: bool,
    escalation_contact: str,
    reaction_seen: str = "\U0001f44c",
    reaction_done: str = "✔️",
) -> Decision:
    """Собирает итоговое решение и план действий по одному сообщению."""
    article = kb.get(verdict.kb_slug) if verdict.kb_slug else None
    if article is None:
        candidates = kb.for_category(verdict.category.value)
        article = candidates[0] if candidates else None

    resolution = verdict.resolution
    # Статья базы знаний знает предметную область лучше общего правила.
    if article and article.resolution:
        try:
            resolution = Resolution(article.resolution)
        except ValueError:
            pass

    entities = dict(verdict.entities)
    missing = missing_entities(resolution, entities)
    summary = _summary(verdict, article)
    title = _title(msg, verdict, article)

    decision = Decision(
        category=verdict.category,
        resolution=resolution,
        confidence=verdict.confidence,
        classified_by="rules" if verdict.decided else "rules+llm",
        title=title,
        summary=summary,
        entities=entities,
        kb_slug=article.slug if article else None,
    )

    # ---- 1. Не заявка: молчим совсем -----------------------------------
    if verdict.category in SILENT_CATEGORIES:
        decision.resolution = Resolution.ignore
        decision.actions = [PlannedAction(
            ActionKind.note,
            {"reason": verdict.category.value},
            "Это объявление или благодарность, а не заявка — реакция не нужна",
            requires_approval=False,
        )]
        return decision

    # ---- 2. Реакция «увидел» в чате заявок -----------------------------
    if is_request_channel:
        decision.actions.append(PlannedAction(
            ActionKind.react_seen,
            {"chat_external_id": msg.chat.external_id,
             "message_external_id": msg.external_id,
             "emoji": reaction_seen},
            "Заявка увидена: помечаем реакцией, в чат не пишем",
        ))

    # ---- 3. Не хватает данных: уточняем в личке -------------------------
    if missing:
        decision.actions.append(PlannedAction(
            ActionKind.send_dm,
            {"to_name": msg.author_name,
             "text": _clarify_text(missing, article),
             "missing": missing},
            "Для решения не хватает: "
            + ", ".join(ENTITY_NAMES.get(m, m).replace("вашу ", "").replace("номер ", "")
                        for m in missing)
            + " — спрашиваем в личке",
        ))
        # Решение НЕ подменяем: заявка по-прежнему требует того же действия,
        # просто пока не хватает данных. Иначе после ответа пользователя
        # система забудет, что вообще собиралась делать.
        decision.blocked_on = list(missing)
        return decision

    # ---- 4. Действуем по решению ----------------------------------------
    if resolution == Resolution.escalate_email:
        email = entities.get("email", "")
        decision.actions.append(PlannedAction(
            ActionKind.forward_email,
            {"to": escalation_contact, "email": email,
             "requester": msg.author_name,
             "text": FORWARD_TEMPLATE.format(
                 requester=msg.author_name, summary=summary, email=email)},
            f"Учётными записями занимается «{escalation_contact}» — передаём туда",
        ))
        decision.actions.append(PlannedAction(
            ActionKind.send_dm,
            {"to_name": msg.author_name, "text": WAIT_TEMPLATE.format(email=email)},
            "Сообщаем пользователю, что заявка принята и нужно подождать",
        ))
        return decision

    if resolution == Resolution.remote_script and article and article.script:
        decision.actions.append(PlannedAction(
            ActionKind.aspia_script,
            {"script": article.script,
             "access_code": entities.get("aspia_code", ""),
             "params": {k: v for k, v in entities.items() if k in {"room", "printer_name"}}},
            f"Решается удалённо: сценарий «{article.script}» через Aspia",
        ))
        decision.actions.append(PlannedAction(
            ActionKind.send_dm,
            {"to_name": msg.author_name,
             "text": CONFIRM_TEMPLATE.format(done=article.title[0].lower() + article.title[1:])},
            "Просим пользователя подтвердить, что проблема решена",
        ))
        return decision

    if resolution == Resolution.auto_answer and article and article.answer:
        decision.actions.append(PlannedAction(
            ActionKind.send_dm,
            {"to_name": msg.author_name, "text": article.answer,
             "kb_slug": article.slug},
            f"Ответ есть в базе знаний: «{article.title}»",
        ))
        return decision

    if resolution == Resolution.need_clarify:
        text = article.answer if (article and article.answer) else _clarify_text([], article)
        decision.actions.append(PlannedAction(
            ActionKind.send_dm,
            {"to_name": msg.author_name, "text": text,
             "kb_slug": article.slug if article else None},
            "Отвечаем из базы знаний и уточняем детали в личке",
        ))
        return decision

    # ---- 5. Нужен человек ------------------------------------------------
    decision.resolution = Resolution.needs_human
    decision.actions.append(PlannedAction(
        ActionKind.note,
        {"reason": "needs_human", "room": entities.get("room", "")},
        "Требуется присутствие на месте: заявка помечена принятой и ждёт человека",
        requires_approval=False,
    ))
    if not is_request_channel:
        # В личке пометить принятие реакцией — единственный способ дать знать,
        # что заявка увидена, не обещая решить её сейчас.
        decision.actions.append(PlannedAction(
            ActionKind.react_seen,
            {"chat_external_id": msg.chat.external_id,
             "message_external_id": msg.external_id,
             "emoji": reaction_seen},
            "Личное обращение сложнее, чем бот умеет: помечаем принятым",
        ))
    return decision


def _summary(verdict: RuleVerdict, article: Article | None) -> str:
    parts = [article.title] if article else []
    parts.extend(n for n in verdict.notes if n and not n.startswith("Решено"))
    return "; ".join(parts) or verdict.category.value


def _title(msg: IncomingMessage, verdict: RuleVerdict, article: Article | None) -> str:
    """Короткий заголовок тикета: категория плюс кто и откуда."""
    base = article.title if article else verdict.category.value
    room = verdict.entities.get("room")
    tail = f", каб. {room}" if room else ""
    return f"{base} — {msg.author_name}{tail}"[:200]


def done_reaction_action(msg_chat_id: str, msg_id: str, emoji: str) -> PlannedAction:
    """Реакция «закрыто» — ставится, когда пользователь подтвердил решение."""
    return PlannedAction(
        ActionKind.react_done,
        {"chat_external_id": msg_chat_id, "message_external_id": msg_id, "emoji": emoji},
        "Пользователь подтвердил, что всё работает — помечаем заявку закрытой",
    )
