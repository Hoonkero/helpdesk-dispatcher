"""Конфигурация приложения. Всё через переменные окружения / .env."""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, Enum):
    """Режим автономности бота.

    shadow   — бот ничего не делает во внешнем мире, только предлагает.
    assisted — реакции ставит сам, сообщения и скрипты — через подтверждение.
    auto     — полный автопилот для категорий из AUTO_CATEGORIES.
    """

    shadow = "shadow"
    assisted = "assisted"
    auto = "auto"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #: Глобальный предохранитель. Пока он включён, ни одно действие не уходит
    #: во внешний мир: исполнители возвращают результат «сухого прогона», а
    #: действие помечается как пропущенное с пояснением. Снимать только вручную.
    safe_mode: bool = True
    dispatcher_mode: Mode = Mode.shadow

    max_transport: str = "fake"
    max_web_url: str = "https://web.max.ru"
    max_profile_dir: Path = Path("./data/max-profile")
    max_poll_interval: int = 15
    max_watch_chats: str = "ИТ Заявки"
    max_ignore_muted: bool = True
    max_reaction_seen: str = "👌"
    max_reaction_done: str = "✔️"
    max_max_actions_per_hour: int = 60

    llm_enabled: bool = True
    llm_base_url: str = "http://127.0.0.1:1234/v1"
    llm_model: str = "qwen2.5-7b-instruct"
    llm_timeout: int = 60

    aspia_enabled: bool = False
    aspia_client_path: str = ""
    aspia_dry_run: bool = True

    escalation_contact: str = "Вторая линия поддержки"

    database_url: str = "sqlite:///./data/dezhurny.db"
    kb_dir: Path = Path("./kb")

    @property
    def watch_chats(self) -> list[str]:
        return [c.strip() for c in self.max_watch_chats.split(",") if c.strip()]


settings = Settings()
