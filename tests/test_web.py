"""Тесты интерфейса оператора: страницы открываются, одобрение работает."""
from __future__ import annotations

import unittest

from app.config import Mode, Settings
from app.domain import ActionStatus
from app.store import Store
from app.transports.fake import FakeMaxTransport
from app.web.app import create_app


class WebCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Settings(max_watch_chats="ИТ Заявки", safe_mode=True,
                            llm_enabled=False, dispatcher_mode=Mode.shadow)
        self.store = Store(":memory:")
        self.transport = FakeMaxTransport()
        self.app = create_app(self.cfg, store=self.store, transport=self.transport)
        self.client = self.app.test_client()
        self.client.post("/poll")


class TestPages(WebCase):
    def test_all_pages_open(self) -> None:
        for url in ("/", "/tickets", "/tickets?status=clarifying", "/actions",
                    "/ticket/1"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_missing_ticket_is_404(self) -> None:
        self.assertEqual(self.client.get("/ticket/9999").status_code, 404)

    def test_safe_mode_banner_is_visible(self) -> None:
        """Оператор обязан видеть, что бот ничего не отправляет."""
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Предохранитель включён", html)

    def test_action_shows_the_exact_text_that_will_be_sent(self) -> None:
        """Нельзя одобрять сообщение, не видя его текста."""
        html = self.client.get("/actions").get_data(as_text=True)
        self.assertIn("Здравствуйте", html)

    def test_statuses_are_labelled_with_words(self) -> None:
        """Правило дизайн-системы: статус подписан словом, а не только цветом."""
        html = self.client.get("/tickets").get_data(as_text=True)
        self.assertTrue(any(word in html for word in ("Новая", "Уточняем", "Принята")))


class TestApproval(WebCase):
    def first_pending(self) -> int:
        rows = self.store.pending_actions()
        self.assertTrue(rows, "очередь не должна быть пустой")
        return int(rows[0]["id"])

    def test_approve_under_safe_mode_does_not_reach_max(self) -> None:
        action_id = self.first_pending()
        self.client.post(f"/action/{action_id}/approve", follow_redirects=True)
        row = self.store.action(action_id)
        self.assertEqual(row["status"], ActionStatus.skipped.value)
        self.assertEqual(self.transport.reactions, [])
        self.assertEqual(self.transport.sent, [])

    def test_reject_marks_action_and_logs_it(self) -> None:
        action_id = self.first_pending()
        ticket_id = int(self.store.action(action_id)["ticket_id"])
        self.client.post(f"/action/{action_id}/reject", follow_redirects=True)
        self.assertEqual(self.store.action(action_id)["status"],
                         ActionStatus.rejected.value)
        import json
        log = json.loads(self.store.ticket(ticket_id)["work_log"])
        self.assertTrue(any("отклонил" in e["text"] for e in log))

    def test_manual_close(self) -> None:
        self.client.post("/ticket/1/close", follow_redirects=True)
        self.assertEqual(self.store.ticket(1)["status"], "closed_by_human")

    def test_unknown_action_is_404(self) -> None:
        self.assertEqual(self.client.post("/action/9999/approve").status_code, 404)


if __name__ == "__main__":
    unittest.main()
