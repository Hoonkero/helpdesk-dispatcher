"""Классификация по правилам и извлечение сущностей из текста заявки.

Почему правила идут первыми, а не модель. Восемь-девять из десяти обращений в
чате поддержки — это типовые фразы: «нет доступа», «тонер», «обновить макс».
Правила отвечают на них мгновенно, предсказуемо и одинаково каждый раз, их можно
прочитать глазами и поправить. Локальная модель нужна там, где правила молчат
или сомневаются, — а не вместо них.

Все шаблоны собраны в одном месте намеренно: это та часть проекта, которую
придётся править чаще всего, по мере того как в чате появляются новые
формулировки.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.domain import Category, Resolution

# --------------------------------------------------------------------- сущности

_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

#: Код доступа Aspia: 6–14 цифр, часто записан группами через пробел или дефис.
_RE_ASPIA = re.compile(
    r"(?:аспи\w*|aspia|код\w*(?:\s+доступа)?|id)\D{0,24}((?:\d[\s\-]?){6,14})",
    re.IGNORECASE,
)
#: Отдельно стоящая длинная группа цифр — тоже похожа на код доступа.
_RE_ASPIA_BARE = re.compile(r"(?<!\d)((?:\d[\s\-]?){8,14})(?!\d)")

#: Кабинет: «в 214», «кабинет 305», «305 кабинете», «в с 306».
_RE_ROOM = re.compile(
    r"(?:кабинет\w*|каб\.?|ауд\w*|в\s+с|в)\s*№?\s*(\d{1,4}[а-яa-z]?)"
    r"|(\d{1,4}[а-яa-z]?)\s*(?:кабинет\w*|каб\.?)",
    re.IGNORECASE,
)
_RE_PHONE = re.compile(r"(?:\+7|8)[\s\-(]?\d{3}[\s\-)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")


def normalize(text: str) -> str:
    """Приводит текст к виду, удобному для сопоставления шаблонов."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip().lower()


def _clean_digits(raw: str) -> str:
    return re.sub(r"\D", "", raw)


def extract_entities(text: str) -> dict:
    """Вытаскивает из сообщения всё, что пригодится для решения заявки.

    Возвращает только найденное: отсутствующий ключ означает «в сообщении этого
    нет», и именно по этому политика решает, нужно ли уточнять в личке.
    """
    found: dict = {}
    if m := _RE_EMAIL.search(text or ""):
        found["email"] = m.group(0)

    code = ""
    if m := _RE_ASPIA.search(text or ""):
        code = _clean_digits(m.group(1))
    elif m := _RE_ASPIA_BARE.search(text or ""):
        # Отдельную группу цифр считаем кодом только если рядом нет слова «кабинет»
        # и это не похоже на телефон.
        candidate = _clean_digits(m.group(1))
        if len(candidate) >= 8 and not _RE_PHONE.search(text or ""):
            code = candidate
    if 6 <= len(code) <= 14:
        found["aspia_code"] = code

    norm = normalize(text)
    for m in _RE_ROOM.finditer(norm):
        room = m.group(1) or m.group(2)
        # «в 8:20» и прочее время — не кабинет; отсекаем однозначный мусор.
        if room and not room.isalpha() and 1 <= len(room) <= 4:
            if room not in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}:
                found["room"] = room
                break

    if m := _RE_PHONE.search(text or ""):
        found["phone"] = m.group(0)
    return found


# ---------------------------------------------------------------------- правила


@dataclass(frozen=True, slots=True)
class Rule:
    """Одно правило: набор шаблонов, категория и вес совпадения."""

    category: Category
    resolution: Resolution
    patterns: tuple[str, ...]
    weight: int = 10
    kb_slug: str | None = None
    note: str = ""

    def score(self, norm_text: str) -> int:
        hits = sum(1 for p in self.patterns if re.search(p, norm_text))
        return self.weight * hits


#: Порядок в списке не важен — решает суммарный вес. Правила с большим весом
#: выигрывают у общих: «обновить макс» конкретнее, чем просто «обновить».
RULES: tuple[Rule, ...] = (
    # --- доступы -----------------------------------------------------------
    Rule(
        Category.access_blocked, Resolution.escalate_email,
        (r"учет\w*\s+запис\w*\s+(?:отключен|заблокир|не\s+актив)",
         r"(?:учетка|аккаунт)\w*\s+(?:в\s+блок|заблокир|отключ)",
         r"в\s+блоке", r"заблокирован\w*\s+(?:учет|аккаунт|доступ)",
         r"не\s+могу\s+(?:войти|зайти)\s+в\s+(?:систему|учет|почт)"),
        weight=14, kb_slug="access-blocked",
        note="Блокировку учётной записи снимает администратор домена",
    ),
    Rule(
        Category.access_blocked, Resolution.escalate_email,
        (r"нет\s+доступ\w*", r"доступ\w*\s+(?:закрыт|нет|пропал)",
         r"отказано\s+в\s+доступе"),
        weight=8, kb_slug="access-blocked",
        note="Формулировка размытая: без уточнения непонятно, к чему именно нет доступа",
    ),
    Rule(
        Category.password_reset, Resolution.escalate_email,
        (r"забыл\w*\s+пароль", r"сброс\w*\s+пароль", r"пароль\w*\s+не\s+подходит",
         r"сменить\s+пароль", r"не\s+помню\s+пароль"),
        weight=14, kb_slug="password-reset",
    ),
    # --- печать ------------------------------------------------------------
    Rule(
        Category.printer, Resolution.needs_human,
        (r"тонер", r"картридж", r"бледно\s+печат", r"полос\w*\s+при\s+печат",
         r"заканчива\w*\s+(?:тонер|краск)"),
        weight=14, kb_slug="printer-toner",
        note="Замена расходников требует присутствия на месте",
    ),
    Rule(
        Category.printer, Resolution.remote_script,
        (r"(?:установ|подключ|переустанов)\w*\s+принтер", r"принтер\w*\s+не\s+(?:виден|найден)",
         r"добавить\s+принтер", r"поставить\s+принтер"),
        weight=15, kb_slug="printer-install",
        note="Подключение сетевого принтера делается удалённо",
    ),
    Rule(
        Category.printer, Resolution.need_clarify,
        (r"принтер", r"не\s+печата\w*", r"печать\s+не\s+идет", r"очередь\s+печати"),
        weight=7, kb_slug="printer-toner",
    ),
    # --- программы ---------------------------------------------------------
    Rule(
        Category.software_install, Resolution.remote_script,
        (r"обнов\w*\s+макс", r"макс\w*\s+(?:стар\w*\s+верси|не\s+обновля)",
         r"обнов\w*\s+max", r"устарел\w*\s+верси\w*\s+макс"),
        weight=15, kb_slug="max-update",
    ),
    Rule(
        Category.software_install, Resolution.remote_script,
        (r"(?:установ|переустанов)\w*\s+(?:программ|приложени|офис|word|excel)",
         r"нужно\s+установить", r"обнов\w*\s+(?:программ|приложени)"),
        weight=11, kb_slug="max-update",
    ),
    Rule(
        Category.software_install, Resolution.need_clarify,
        (r"^обновить$", r"^обновить\b", r"\bобнови(?:те|ть)\b"),
        weight=6, kb_slug="max-update",
        note="Сказано только «обновить» — неясно что именно",
    ),
    # --- сервисы -----------------------------------------------------------
    Rule(
        Category.cloud_access, Resolution.need_clarify,
        (r"клауд", r"cloud", r"не\s+(?:могу|получается)\s+зай\w*\s+в\s+клауд",
         r"помогите\s+зайти"),
        weight=13, kb_slug="cloud-access",
    ),
    Rule(
        Category.cloud_access, Resolution.need_clarify,
        (r"не\s+заход\w*\s+(?:в|на)\s+\w+", r"не\s+пуска\w*\s+в\s+\w+",
         r"не\s+могу\s+войти"),
        weight=7, kb_slug="cloud-access",
    ),
    # --- железо ------------------------------------------------------------
    Rule(
        Category.hardware, Resolution.needs_human,
        (r"смарт\w*\s*панел", r"интерактивн\w*\s+доск", r"проектор",
         r"нет\s+сигнала", r"не\s+показыва\w*\s+изображени",
         r"не\s+реагиру\w*\s+на\s+касани"),
        weight=14, kb_slug="smartboard",
        note="Панели и доски требуют физического присутствия",
    ),
    Rule(
        Category.hardware, Resolution.needs_human,
        (r"не\s+включа\w*\s+(?:компьютер|моноблок|ноутбук|пк)",
         r"(?:сгорел|не\s+работает)\s+(?:блок|монитор|мышь|клавиатура)",
         r"монитор\w*\s+(?:черн|не\s+горит)", r"шумит\s+(?:компьютер|системник)"),
        weight=12,
    ),
    # --- сеть --------------------------------------------------------------
    Rule(
        Category.network, Resolution.need_clarify,
        (r"не\s+работает\s+интернет", r"нет\s+интернета", r"wi[\s\-]?fi",
         r"не\s+подключа\w*\s+к\s+сети", r"пропал\w*\s+(?:сеть|интернет)"),
        weight=14, kb_slug="network",
    ),
    # --- вопросы -----------------------------------------------------------
    Rule(
        Category.howto, Resolution.need_clarify,
        (r"не\s+понимаю", r"как\s+(?:мне\s+)?(?:сделать|поставить|настроить|зайти|отправить)",
         r"подскажите\s+как", r"не\s+разберусь", r"куда\s+нажать"),
        weight=6,
        note="Общий вопрос: уступает предметным правилам, если в тексте есть конкретика",
    ),
    # --- не заявки ---------------------------------------------------------
    Rule(
        Category.info_broadcast, Resolution.ignore,
        (r"напомина\w*[:,]", r"^добрый\s+день!?\s+(?:изменени|информаци)",
         r"^внимание\b", r"^уважаемые\s+коллеги", r"вход\s+через\s+\w+\s+холл",
         r"^объявлени"),
        weight=13,
        note="Объявление, а не обращение",
    ),
    Rule(
        Category.smalltalk, Resolution.ignore,
        (r"^спасибо\b", r"\bспасибо\b.*\b(?:работает|помогло|все\s+ок)",
         r"^(?:ок|окей|хорошо|понятно|ясно|принято)[\s!.)]*$",
         r"все\s+(?:работает|ок|получилось)", r"^\+\s*$", r"^ага\b"),
        weight=13,
        note="Благодарность или подтверждение, реагировать не нужно",
    ),
)

#: Что должно быть известно, чтобы решение вообще было выполнимо.
REQUIRED_ENTITIES: dict[Resolution, tuple[str, ...]] = {
    Resolution.remote_script: ("aspia_code",),
    Resolution.escalate_email: ("email",),
    Resolution.needs_human: ("room",),
}


@dataclass(slots=True)
class RuleVerdict:
    """Результат работы правил. confidence=0 означает «правила не сработали»."""

    category: Category
    resolution: Resolution
    confidence: float
    kb_slug: str | None
    entities: dict
    notes: list[str]
    matched: list[str]

    @property
    def decided(self) -> bool:
        """Достаточно ли уверенно, чтобы не звать модель."""
        return self.confidence >= 0.6


def classify_by_rules(text: str) -> RuleVerdict:
    """Сопоставляет текст со всеми правилами и возвращает лучшую версию.

    Уверенность считается как доля веса победителя в сумме весов всех
    сработавших правил: если сработало одно правило — уверенность высокая,
    если несколько разных категорий тянут в свои стороны — низкая, и решение
    уйдёт в модель.
    """
    norm = normalize(text)
    entities = extract_entities(text)
    if not norm:
        return RuleVerdict(Category.unknown, Resolution.needs_human, 0.0, None,
                           entities, ["Пустое сообщение"], [])

    scored: list[tuple[int, Rule]] = []
    for rule in RULES:
        s = rule.score(norm)
        if s:
            scored.append((s, rule))
    if not scored:
        return RuleVerdict(Category.unknown, Resolution.needs_human, 0.0, None,
                           entities, ["Правила не сработали"], [])

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top = scored[0]
    total = sum(s for s, _ in scored)
    # Совпадения внутри одной категории не считаем конфликтом.
    same_cat = sum(s for s, r in scored if r.category == top.category)
    confidence = round(min(0.99, same_cat / total), 2)

    notes = [r.note for _, r in scored if r.note and r.category == top.category]
    matched = [f"{r.category.value}:{s}" for s, r in scored[:4]]
    return RuleVerdict(top.category, top.resolution, confidence, top.kb_slug,
                       entities, notes, matched)


def missing_entities(resolution: Resolution, entities: dict) -> list[str]:
    """Каких данных не хватает, чтобы выполнить это решение."""
    return [k for k in REQUIRED_ENTITIES.get(resolution, ()) if not entities.get(k)]
