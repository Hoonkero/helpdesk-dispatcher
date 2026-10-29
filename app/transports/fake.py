"""Транспорт-заглушка: типовые сценарии заявок, без сети и браузера.

Нужен для тестов, для разработки веб-интерфейса и для прогонов «а что бот
сделал бы» до подключения живой сессии.
"""
from __future__ import annotations

from datetime import timedelta

from app.domain import ChatInfo, IncomingMessage, utcnow
from app.transports.base import MaxTransport, WriteForbidden

IT_MAIN = ChatInfo("chat-it-main", "ИТ Заявки", is_group=True, is_muted=False)
IT_BRANCH = ChatInfo("chat-it-branch", "ИТ Филиал", is_group=True, is_muted=True)
BOLTALKA = ChatInfo("chat-boltalka", "Болталка", is_group=True, is_muted=True)
DM_OLGA = ChatInfo("dm-olga", "Демо-сотрудник 11", is_group=False, is_muted=False)
DM_VIKTOR = ChatInfo("dm-viktor", "Демо-сотрудник 02", is_group=False, is_muted=False)
DM_MAXIM = ChatInfo("dm-maxim", "Демо-сотрудник 08", is_group=False, is_muted=False)


def _seed() -> dict[str, list[IncomingMessage]]:
    """Сообщения, типовые формулировки заявок (имена вымышленные)."""
    base = utcnow() - timedelta(minutes=90)

    def msg(chat: ChatInfo, n: int, author: str, text: str, mins: int) -> IncomingMessage:
        return IncomingMessage(
            chat=chat,
            external_id=f"{chat.external_id}-m{n}",
            author_name=author,
            text=text,
            sent_at=base + timedelta(minutes=mins),
        )

    return {
        IT_MAIN.external_id: [
            msg(IT_MAIN, 1, "Демо-сотрудник 04", "Добрый день, нужна помощь со смарт панелью в 214, не показывает изображение", 0),
            msg(IT_MAIN, 2, "Демо-сотрудник 11", "Хэлп, опять что-то с тонером", 5),
            msg(IT_MAIN, 3, "Демо-сотрудник 08", "Всем привет. Учетная запись отключена, не могу войти", 12),
            msg(IT_MAIN, 4, "Демо-сотрудник 01", "Помогите зайти в Клауд", 20),
            msg(IT_MAIN, 5, "Демо-сотрудник 05", "Напоминаю: вход через центральный holl до 8:20", 25),
            msg(IT_MAIN, 6, "Демо-сотрудник 07", "Нет доступа", 30),
            msg(IT_MAIN, 7, "Демо-сотрудник 03", "Нужно обновить макс, у меня старая версия не открывает файлы", 35),
            msg(IT_MAIN, 8, "Демо-сотрудник 13", "спасибо, всё работает", 40),
            msg(IT_MAIN, 9, "Демо-сотрудник 12", "Не понимаю как поставить принтер на новый компьютер", 45),
        ],
        DM_VIKTOR.external_id: [
            msg(DM_VIKTOR, 1, "Демо-сотрудник 02", "ivanova.a@example.org Привет в блоке человек)", 50),
        ],
        DM_OLGA.external_id: [
            msg(DM_OLGA, 1, "Демо-сотрудник 11", "Это опять я, тонер в 305 кабинете", 55),
        ],
        DM_MAXIM.external_id: [
            msg(DM_MAXIM, 1, "Демо-сотрудник 08", "код аспии 123 456 789, можешь глянуть", 60),
        ],
        IT_BRANCH.external_id: [
            msg(IT_BRANCH, 1, "Кто-то", "Это приглушённый чат, бот его читать не должен", 65),
        ],
        BOLTALKA.external_id: [
            msg(BOLTALKA, 1, "Кто-то", "И этот тоже", 70),
        ],
    }


class FakeMaxTransport(MaxTransport):
    """Хранит всё в памяти и записывает, что бот попытался сделать."""

    def __init__(self) -> None:
        self._chats = [IT_MAIN, IT_BRANCH, BOLTALKA, DM_OLGA, DM_VIKTOR, DM_MAXIM]
        self._messages = _seed()
        self.started = False
        #: журнал внешних эффектов — по нему тесты проверяют поведение
        self.reactions: list[tuple[str, str, str]] = []
        self.sent: list[tuple[str, str, str | None]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def is_authenticated(self) -> bool:
        return True

    def list_chats(self) -> list[ChatInfo]:
        return list(self._chats)

    def fetch_new_messages(self, chat: ChatInfo, limit: int = 30) -> list[IncomingMessage]:
        return list(self._messages.get(chat.external_id, []))[-limit:]

    def add_reaction(self, chat: ChatInfo, message_external_id: str, emoji: str) -> None:
        self.reactions.append((chat.external_id, message_external_id, emoji))

    def send_message(self, chat: ChatInfo, text: str, reply_to: str | None = None) -> str:
        if chat.is_group:
            raise WriteForbidden(f"В групповой чат «{chat.title}» бот писать не должен")
        self.sent.append((chat.external_id, text, reply_to))
        return f"{chat.external_id}-out{len(self.sent)}"

    # ------------------------------------------------------- помощь тестам
    def inject(self, chat: ChatInfo, author: str, text: str) -> IncomingMessage:
        """Добавить новое входящее сообщение (имитация ответа пользователя)."""
        bucket = self._messages.setdefault(chat.external_id, [])
        m = IncomingMessage(
            chat=chat,
            external_id=f"{chat.external_id}-m{len(bucket) + 1}",
            author_name=author,
            text=text,
        )
        bucket.append(m)
        return m
