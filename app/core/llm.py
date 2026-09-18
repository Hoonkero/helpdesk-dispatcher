"""Клиент локальной модели (LM Studio / Ollama) через OpenAI-совместимый API.

Используется только там, где правила не справились. Работает на стандартной
библиотеке: никаких SDK, чтобы проект поднимался на чистом Python.

Главный принцип: модель — необязательная деталь. Если она недоступна, медлит
или вернула мусор, методы возвращают None, и вызывающий продолжает работать на
правилах. Бот не должен останавливаться из-за того, что кто-то выключил
LM Studio.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.domain import Category

log = logging.getLogger(__name__)

#: Модель обязана выбирать только из этого списка — иначе ответ отбрасывается.
_ALLOWED = tuple(c.value for c in Category)

SYSTEM_PROMPT = """Ты — классификатор обращений в чате IT-поддержки организации.
Тебе дают одно сообщение сотрудника. Определи, о чём оно.

Отвечай ТОЛЬКО одним объектом JSON, без пояснений и без markdown:
{"category": "...", "confidence": 0.0, "summary": "...", "entities": {}}

Допустимые значения category:
- access_blocked   — учётная запись заблокирована или нет доступа к системе
- password_reset   — забыл пароль, нужен сброс
- printer          — принтер, печать, тонер, картридж
- software_install — установить или обновить программу
- cloud_access     — не получается зайти в сервис (Клауд и подобные)
- hardware         — техника: панель, доска, монитор, компьютер не включается
- network          — интернет, wi-fi, сеть
- howto            — вопрос «как сделать», человек не понимает интерфейс
- info_broadcast   — объявление для всех, а не обращение
- smalltalk        — благодарность, «ок», «всё работает», болтовня
- unknown          — понять невозможно

confidence — число от 0 до 1, насколько ты уверен.
summary — одна короткая фраза по-русски: в чём суть проблемы.
entities — только то, что явно есть в тексте, из ключей:
  email, room (номер кабинета), aspia_code (код удалённого доступа).
Не придумывай значения, которых в сообщении нет."""


@dataclass(slots=True)
class LlmVerdict:
    category: Category
    confidence: float
    summary: str
    entities: dict


class LocalLlm:
    """Тонкая обёртка над /chat/completions локального сервера моделей."""

    def __init__(self, base_url: str, model: str, *, timeout: int = 60,
                 enabled: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.enabled = enabled
        self._unavailable_logged = False

    # ------------------------------------------------------------------ связь
    def available(self) -> bool:
        """Отвечает ли сервер моделей. Не бросает исключений."""
        if not self.enabled:
            return False
        try:
            with urllib.request.urlopen(f"{self.base_url}/models", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def _post(self, payload: dict) -> dict | None:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            log.warning("модель ответила HTTP %s: %s", e.code, e.read()[:200])
        except Exception as e:
            if not self._unavailable_logged:
                log.warning("локальная модель недоступна (%s): работаем на правилах", e)
                self._unavailable_logged = True
        return None

    # -------------------------------------------------------------- разбор
    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Достаёт объект JSON из ответа, даже если модель обернула его в текст."""
        text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE)
        start = text.find("{")
        while start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find("{", start + 1)
        return None

    # --------------------------------------------------------------- работа
    def classify(self, text: str) -> LlmVerdict | None:
        """Классифицирует сообщение. None — модель не помогла, решают правила."""
        if not self.enabled:
            return None
        data = self._post({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": 300,
        })
        if not data:
            return None
        try:
            raw = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            log.warning("неожиданная форма ответа модели")
            return None

        parsed = self._extract_json(raw)
        if not parsed:
            log.warning("модель вернула не JSON: %s", str(raw)[:160])
            return None

        category = str(parsed.get("category", "")).strip()
        if category not in _ALLOWED:
            log.warning("модель вернула неизвестную категорию %r", category)
            return None

        try:
            confidence = float(parsed.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        entities = parsed.get("entities")
        if not isinstance(entities, dict):
            entities = {}
        # Модель не имеет права выдумывать сущности: оставляем только те
        # значения, которые буквально встречаются в исходном тексте.
        clean = {
            k: str(v) for k, v in entities.items()
            if k in {"email", "room", "aspia_code"} and v
            and str(v).lower() in (text or "").lower()
        }

        summary = str(parsed.get("summary") or "").strip()[:300]
        return LlmVerdict(Category(category), confidence, summary, clean)

    def draft_clarification(self, text: str, missing: list[str]) -> str | None:
        """Черновик уточняющего вопроса. Не применяется без проверки оператором."""
        if not self.enabled or not missing:
            return None
        human = {"email": "рабочую почту", "room": "номер кабинета",
                 "aspia_code": "код доступа Aspia"}
        need = ", ".join(human.get(m, m) for m in missing)
        data = self._post({
            "model": self.model,
            "messages": [
                {"role": "system", "content":
                 "Ты сотрудник IT-поддержки организации. Напиши одно короткое вежливое "
                 "сообщение в личку на «вы», чтобы уточнить недостающие данные. "
                 "Без приветствий-шаблонов, без эмодзи, максимум два предложения. "
                 "Только текст сообщения, ничего больше."},
                {"role": "user", "content":
                 f"Обращение: {text}\nНужно уточнить: {need}"},
            ],
            "temperature": 0.3,
            "max_tokens": 160,
        })
        if not data:
            return None
        try:
            return str(data["choices"][0]["message"]["content"]).strip()[:500] or None
        except (KeyError, IndexError):
            return None
