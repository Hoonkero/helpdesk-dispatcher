"""Проверка классификации на настоящих формулировках из рабочих чатов.

Эти тесты — главная защита от регрессий: правила придётся править по мере
появления новых формулировок, и после каждой правки должно быть видно, что
старые случаи не сломались.
"""
from __future__ import annotations

import unittest

from app.core.rules import classify_by_rules, extract_entities, missing_entities
from app.domain import Category, Resolution


class TestCategories(unittest.TestCase):
    def assert_category(self, text: str, expected: Category) -> None:
        verdict = classify_by_rules(text)
        self.assertEqual(
            verdict.category, expected,
            f"{text!r}: ожидалась {expected.value}, получена {verdict.category.value}",
        )

    def test_blocked_account(self) -> None:
        for text in (
            "Всем привет. Учетная запись отключена, не могу войти",
            "Привет в блоке человек)",
            "учетка заблокирована",
        ):
            self.assert_category(text, Category.access_blocked)

    def test_printer(self) -> None:
        self.assert_category("Хэлп, опять что-то с тонером", Category.printer)
        self.assert_category("Не понимаю как поставить принтер", Category.printer)
        self.assert_category("принтер бледно печатает", Category.printer)

    def test_software(self) -> None:
        self.assert_category("Нужно обновить макс", Category.software_install)
        self.assert_category("Обновить", Category.software_install)

    def test_hardware_and_network(self) -> None:
        self.assert_category("не показывает изображение на смарт панели", Category.hardware)
        self.assert_category("Не работает интернет в 118", Category.network)

    def test_password(self) -> None:
        self.assert_category("Забыл пароль от рабочей почты", Category.password_reset)

    def test_not_a_request(self) -> None:
        """Объявления и благодарности не должны становиться заявками."""
        self.assert_category("Напоминаю: вход через центральный холл до 8:20",
                             Category.info_broadcast)
        self.assert_category("спасибо, всё работает", Category.smalltalk)
        self.assert_category("Ок", Category.smalltalk)

    def test_unknown_stays_unknown(self) -> None:
        """Бессмыслицу нельзя уверенно отнести к категории."""
        verdict = classify_by_rules("асдфг йцукен")
        self.assertEqual(verdict.category, Category.unknown)
        self.assertEqual(verdict.confidence, 0.0)
        self.assertFalse(verdict.decided)

    def test_empty_message(self) -> None:
        verdict = classify_by_rules("")
        self.assertEqual(verdict.category, Category.unknown)


class TestEntities(unittest.TestCase):
    def test_email(self) -> None:
        found = extract_entities("ivanova.a@example.org Привет в блоке человек)")
        self.assertEqual(found["email"], "ivanova.a@example.org")

    def test_aspia_code_with_spaces(self) -> None:
        self.assertEqual(extract_entities("код аспии 123 456 789")["aspia_code"],
                         "123456789")

    def test_aspia_code_wording_variants(self) -> None:
        for text in ("код доступа 987654321", "aspia 123456789", "мой id 112233445"):
            self.assertIn("aspia_code", extract_entities(text), text)

    def test_room(self) -> None:
        for text, room in (("смарт панель в 214", "214"), ("кабинет 402", "402"),
                           ("тонер в 305 кабинете", "305")):
            self.assertEqual(extract_entities(text).get("room"), room, text)

    def test_time_is_not_a_room(self) -> None:
        """«до 8:20» — это время, а не кабинет."""
        self.assertNotIn("room", extract_entities("вход через холл до 8:20"))

    def test_phone_is_not_aspia_code(self) -> None:
        found = extract_entities("мой телефон +7 999 123 45 67")
        self.assertIn("phone", found)
        self.assertNotIn("aspia_code", found)

    def test_nothing_invented(self) -> None:
        self.assertEqual(extract_entities("просто текст без данных"), {})


class TestMissing(unittest.TestCase):
    def test_escalation_needs_email(self) -> None:
        self.assertEqual(missing_entities(Resolution.escalate_email, {}), ["email"])
        self.assertEqual(
            missing_entities(Resolution.escalate_email, {"email": "a@b.ru"}), [])

    def test_remote_script_needs_code(self) -> None:
        self.assertEqual(missing_entities(Resolution.remote_script, {}), ["aspia_code"])


if __name__ == "__main__":
    unittest.main()
