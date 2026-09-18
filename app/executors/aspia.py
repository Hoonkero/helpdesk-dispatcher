"""Удалённые действия на компьютере пользователя через Aspia.

Самая опасная часть проекта: здесь код запускается на чужой машине. Поэтому
ограничения жёсткие и намеренно неудобные:

* исполняются только сценарии из белого списка SCRIPTS — произвольной команды
  не существует даже как возможности;
* каждый параметр проверяется регулярным выражением, потому что подстановка
  непроверенной строки в команду — это исполнение чужого кода на рабочем
  компьютере сотрудника;
* по умолчанию режим сухого прогона: команда показывается, но не выполняется;
* код доступа Aspia никогда не попадает в журнал — он даёт полный доступ к
  машине.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from app.executors.base import ActionResult

log = logging.getLogger(__name__)

#: Допустимые значения параметров. Всё остальное отклоняется.
VALIDATORS: dict[str, re.Pattern[str]] = {
    "printer_host": re.compile(r"^[A-Za-z0-9._-]{1,63}$"),
    "printer_name": re.compile(r"^[A-Za-z0-9 ._()-]{1,64}$"),
    "room": re.compile(r"^[0-9А-Яа-яA-Za-z-]{1,8}$"),
}

#: Символы, которые не должны встречаться в параметрах ни при каких условиях.
FORBIDDEN = re.compile(r"[;&|`$\"'\n\r<>]")


@dataclass(frozen=True, slots=True)
class Script:
    """Сценарий из белого списка."""

    name: str
    title: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    #: Шаблон команды. Подставляются только проверенные параметры.
    command: tuple[str, ...] = ()
    destructive: bool = False
    defaults: dict[str, str] = field(default_factory=dict)


SCRIPTS: dict[str, Script] = {
    "install_printer": Script(
        name="install_printer",
        title="Подключить сетевой принтер",
        required=("printer_host", "printer_name"),
        command=("powershell", "-NoProfile", "-NonInteractive", "-Command",
                 'Add-Printer -ConnectionName "\\\\{printer_host}\\{printer_name}"'),
        destructive=False,
    ),
    "update_max": Script(
        name="update_max",
        title="Обновить приложение MAX",
        required=(),
        command=("powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "winget upgrade --id MAX.MAX --accept-source-agreements "
                 "--accept-package-agreements --silent"),
        destructive=False,
    ),
}


class UnknownScript(ValueError):
    """Запрошен сценарий вне белого списка."""


class InvalidParameter(ValueError):
    """Параметр не прошёл проверку — команда не собиралась."""


def _mask(code: str) -> str:
    """Маскирует код доступа для журнала, оставляя только длину."""
    return f"***({len(code)} цифр)" if code else "не указан"


class AspiaExecutor:
    """Запускает сценарии белого списка на машине пользователя."""

    def __init__(self, client_path: str = "", *, dry_run: bool = True,
                 timeout: int = 120) -> None:
        self.client_path = client_path
        self.dry_run = dry_run
        self.timeout = timeout

    def available(self) -> bool:
        """Установлен ли клиент Aspia."""
        if not self.client_path:
            return False
        return bool(shutil.which(self.client_path)) or bool(
            __import__("pathlib").Path(self.client_path).is_file())

    # ------------------------------------------------------------- проверки
    @staticmethod
    def validate(script: Script, params: dict) -> dict:
        """Проверяет и нормализует параметры. Бросает InvalidParameter."""
        merged = {**script.defaults, **{k: str(v) for k, v in (params or {}).items() if v}}
        for key in script.required:
            if not merged.get(key):
                raise InvalidParameter(
                    f"для сценария «{script.title}» не хватает параметра {key}")
        checked: dict[str, str] = {}
        for key, value in merged.items():
            if key not in script.required and key not in script.optional:
                continue
            if FORBIDDEN.search(value):
                raise InvalidParameter(
                    f"параметр {key} содержит недопустимые символы и отклонён")
            pattern = VALIDATORS.get(key)
            if pattern and not pattern.match(value):
                raise InvalidParameter(f"параметр {key}={value!r} не прошёл проверку")
            checked[key] = value
        return checked

    def build_command(self, script_name: str, params: dict) -> list[str]:
        """Собирает команду. Отдельный метод, чтобы её можно было проверить тестом."""
        script = SCRIPTS.get(script_name)
        if script is None:
            raise UnknownScript(f"сценарий {script_name!r} не в белом списке")
        checked = self.validate(script, params)
        return [part.format(**checked) for part in script.command]

    # --------------------------------------------------------------- запуск
    def run(self, script_name: str, access_code: str, params: dict) -> ActionResult:
        """Выполняет сценарий. При dry_run только показывает команду."""
        started = time.monotonic()
        try:
            command = self.build_command(script_name, params)
        except (UnknownScript, InvalidParameter) as e:
            log.warning("сценарий %s отклонён: %s", script_name, e)
            return ActionResult.failure(str(e))

        script = SCRIPTS[script_name]
        log.info("сценарий %s (%s), код доступа %s, параметры %s",
                 script.name, script.title, _mask(access_code), params)

        if self.dry_run:
            return ActionResult(
                ok=True, dry_run=True,
                output=f"Сухой прогон: {' '.join(command)}",
                seconds=round(time.monotonic() - started, 2),
            )
        if not access_code:
            return ActionResult.failure("нет кода доступа Aspia — подключиться некуда")

        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return ActionResult.failure(f"сценарий не завершился за {self.timeout} с")
        except OSError as e:
            return ActionResult.failure(f"не удалось запустить: {e}")

        seconds = round(time.monotonic() - started, 2)
        if proc.returncode != 0:
            return ActionResult(ok=False, seconds=seconds,
                                error=(proc.stderr or proc.stdout or "")[-800:])
        return ActionResult(ok=True, seconds=seconds,
                            output=(proc.stdout or "готово")[-800:])
