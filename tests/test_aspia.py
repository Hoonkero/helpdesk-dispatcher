"""Тесты исполнителя Aspia: белый список, проверка параметров, сухой прогон.

Здесь проверяется не удобство, а невозможность выстрелить себе в ногу: код
исполняется на компьютере сотрудника, и обойти ограничения не должно
получаться даже намеренно.
"""
from __future__ import annotations

import logging
import unittest
from unittest import mock

from app.executors.aspia import (
    SCRIPTS,
    AspiaExecutor,
    InvalidParameter,
    UnknownScript,
)

GOOD = {"printer_host": "srv-print", "printer_name": "HP LJ 1320"}


class TestWhitelist(unittest.TestCase):
    def test_unknown_script_rejected(self) -> None:
        ex = AspiaExecutor(dry_run=True)
        with self.assertRaises(UnknownScript):
            ex.build_command("delete_everything", {})

    def test_unknown_script_run_returns_error_not_exception(self) -> None:
        """run() не должен падать: заявка обязана получить внятный отказ."""
        result = AspiaExecutor(dry_run=True).run("delete_everything", "123456", {})
        self.assertFalse(result.ok)
        self.assertIn("белом списке", result.error)

    def test_only_two_scripts_exist(self) -> None:
        self.assertEqual(set(SCRIPTS), {"install_printer", "update_max"})
        for script in SCRIPTS.values():
            self.assertFalse(script.destructive)


class TestParameterValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.ex = AspiaExecutor(dry_run=True)

    def test_command_injection_rejected(self) -> None:
        for bad in ("HP; rm -rf /", "HP && shutdown", "HP`whoami`",
                    "HP$(id)", 'HP" -Force "', "HP\nAdd-Printer"):
            with self.assertRaises(InvalidParameter, msg=bad):
                self.ex.build_command("install_printer",
                                      {**GOOD, "printer_name": bad})

    def test_missing_required_parameter(self) -> None:
        with self.assertRaises(InvalidParameter) as ctx:
            self.ex.build_command("install_printer", {"printer_host": "srv"})
        self.assertIn("printer_name", str(ctx.exception))

    def test_valid_parameters_build_expected_command(self) -> None:
        command = self.ex.build_command("install_printer", GOOD)
        self.assertEqual(command[0], "powershell")
        self.assertIn("Add-Printer", command[-1])
        self.assertIn("srv-print", command[-1])

    def test_unexpected_parameters_ignored(self) -> None:
        """Лишний параметр не должен попадать в команду."""
        command = self.ex.build_command(
            "install_printer", {**GOOD, "evil": "; rm -rf /"})
        self.assertNotIn("rm -rf", command[-1])


class TestDryRun(unittest.TestCase):
    def test_dry_run_never_starts_a_process(self) -> None:
        with mock.patch("subprocess.run") as run:
            result = AspiaExecutor(dry_run=True).run("install_printer", "123456789", GOOD)
        run.assert_not_called()
        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertIn("Сухой прогон", result.output)

    def test_real_run_requires_access_code(self) -> None:
        with mock.patch("subprocess.run") as run:
            result = AspiaExecutor(dry_run=False).run("install_printer", "", GOOD)
        run.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIn("кода доступа", result.error)

    def test_real_run_calls_process_once(self) -> None:
        completed = mock.Mock(returncode=0, stdout="Принтер добавлен", stderr="")
        with mock.patch("subprocess.run", return_value=completed) as run:
            result = AspiaExecutor(dry_run=False).run("install_printer", "123456789", GOOD)
        run.assert_called_once()
        self.assertTrue(result.ok)
        self.assertIn("Принтер добавлен", result.output)

    def test_nonzero_exit_is_failure(self) -> None:
        completed = mock.Mock(returncode=1, stdout="", stderr="Отказано в доступе")
        with mock.patch("subprocess.run", return_value=completed):
            result = AspiaExecutor(dry_run=False).run("update_max", "123456789", {})
        self.assertFalse(result.ok)
        self.assertIn("Отказано", result.error)


class TestLogging(unittest.TestCase):
    def test_access_code_never_logged(self) -> None:
        """Код доступа Aspia даёт полный доступ к чужой машине — в логах его быть нельзя."""
        code = "987654321"
        with self.assertLogs("app.executors.aspia", level=logging.INFO) as logs:
            AspiaExecutor(dry_run=True).run("update_max", code, {})
        joined = "\n".join(logs.output)
        self.assertNotIn(code, joined)
        self.assertIn("***", joined)


if __name__ == "__main__":
    unittest.main()
