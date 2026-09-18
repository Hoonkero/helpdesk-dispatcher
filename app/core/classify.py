"""Каскад классификации: правила, затем — если нужно — локальная модель.

Порядок именно такой, потому что правила дают одинаковый ответ на одинаковый
текст и их можно прочитать глазами. Модель подключается там, где правила
промолчали или засомневались, и её мнение не отменяет уверенное правило —
только дополняет.
"""
from __future__ import annotations

import logging

from app.core.llm import LocalLlm
from app.core.rules import RuleVerdict, classify_by_rules
from app.domain import Category, Resolution

log = logging.getLogger(__name__)

#: Решение по умолчанию для категории, если правило его не подсказало.
DEFAULT_RESOLUTION: dict[Category, Resolution] = {
    Category.access_blocked: Resolution.escalate_email,
    Category.password_reset: Resolution.escalate_email,
    Category.printer: Resolution.need_clarify,
    Category.software_install: Resolution.remote_script,
    Category.cloud_access: Resolution.need_clarify,
    Category.hardware: Resolution.needs_human,
    Category.network: Resolution.need_clarify,
    Category.howto: Resolution.need_clarify,
    Category.info_broadcast: Resolution.ignore,
    Category.smalltalk: Resolution.ignore,
    Category.unknown: Resolution.needs_human,
}


class Classifier:
    """Определяет категорию обращения и предварительное решение."""

    def __init__(self, llm: LocalLlm | None = None) -> None:
        self.llm = llm

    def classify(self, text: str) -> RuleVerdict:
        """Возвращает вердикт с полем `source` в notes: откуда взялось решение."""
        verdict = classify_by_rules(text)
        if verdict.decided or self.llm is None:
            verdict.notes.insert(0, "Решено правилами")
            return verdict

        llm_verdict = self.llm.classify(text)
        if llm_verdict is None:
            verdict.notes.insert(
                0, "Правила сомневаются, модель недоступна — нужна проверка человеком")
            if verdict.confidence < 0.3:
                verdict.category = Category.unknown
                verdict.resolution = Resolution.needs_human
            return verdict

        # Модель уверена сильнее правил — берём её категорию.
        if llm_verdict.confidence > verdict.confidence:
            verdict.notes.insert(
                0, f"Модель уточнила категорию: было {verdict.category.value}, "
                   f"стало {llm_verdict.category.value}")
            verdict.category = llm_verdict.category
            verdict.resolution = DEFAULT_RESOLUTION.get(
                llm_verdict.category, Resolution.needs_human)
            verdict.confidence = round(llm_verdict.confidence, 2)
            verdict.kb_slug = None
        else:
            verdict.notes.insert(0, "Модель согласилась с правилами или была менее уверена")

        # Сущности, найденные моделью, дополняют, но не переписывают найденные regexp.
        for key, value in llm_verdict.entities.items():
            verdict.entities.setdefault(key, value)
        if llm_verdict.summary:
            verdict.notes.append(f"Модель: {llm_verdict.summary}")
        return verdict
