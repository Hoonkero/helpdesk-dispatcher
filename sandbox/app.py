"""Локальный стенд: мини-клон веб-клиента MAX для отладки транспорта.

Зачем он нужен. Разрабатывать Playwright-транспорт на живом рабочем аккаунте
нельзя: любая ошибка отправляет сообщение или ставит реакцию настоящим людям, и
отменить это невозможно. Стенд повторяет ту же структуру страницы и те же
действия (список чатов, приглушённые чаты, история, панель реакций, поле ввода),
но все данные здесь выдуманные и живёт всё в памяти процесса.

DOM-контракт. Каждый элемент, который нужен транспорту, помечен атрибутом
`data-testid`. Это и есть контракт между стендом и транспортом: транспорт
обращается только к этим атрибутам через словарь SELECTORS, а при переходе на
настоящий MAX меняются только значения в этом словаре, а не логика.

Запуск:  python -m sandbox.app          (http://127.0.0.1:8765)
Сброс:   POST /api/reset                (вернуть исходные тестовые данные)
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template_string, request, url_for

SEED_PATH = Path(__file__).with_name("seed.json")
#: Набор реакций стенда повторяет рабочий: «увидел» и «закрыто».
REACTION_SET = ["\U0001f44c", "✔️", "❤️", "\U0001f44d"]


class SandboxState:
    """Состояние стенда в памяти: чаты, сообщения, реакции, отправленное."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        self.me: dict = raw["me"]
        now = datetime.now(tz=timezone.utc)
        self.chats: list[dict] = []
        for chat in deepcopy(raw["chats"]):
            for msg in chat["messages"]:
                msg["sent_at"] = (now - timedelta(minutes=msg.pop("minutes_ago"))).isoformat(
                    timespec="seconds"
                )
                msg.setdefault("reactions", [])
                msg.setdefault("own", False)
            self.chats.append(chat)
        #: журнал внешних эффектов — по нему тесты проверяют, что сделал транспорт
        self.audit: list[dict] = []

    def chat(self, chat_id: str) -> dict:
        for chat in self.chats:
            if chat["id"] == chat_id:
                return chat
        abort(404, f"нет чата {chat_id}")

    def message(self, chat_id: str, message_id: str) -> dict:
        for msg in self.chat(chat_id)["messages"]:
            if msg["id"] == message_id:
                return msg
        abort(404, f"нет сообщения {message_id} в {chat_id}")

    def note(self, action: str, **fields: object) -> None:
        self.audit.append(
            {"at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
             "action": action, **fields}
        )

    def toggle_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        """Ставит или снимает реакцию. Возвращает True, если реакция теперь стоит."""
        msg = self.message(chat_id, message_id)
        if emoji in msg["reactions"]:
            msg["reactions"].remove(emoji)
            self.note("reaction_removed", chat=chat_id, message=message_id, emoji=emoji)
            return False
        msg["reactions"].append(emoji)
        self.note("reaction_added", chat=chat_id, message=message_id, emoji=emoji)
        return True

    def send(self, chat_id: str, text: str) -> dict:
        chat = self.chat(chat_id)
        msg = {
            "id": f"out{len(chat['messages']) + 1}",
            "author": self.me["name"],
            "text": text,
            "sent_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "reactions": [],
            "own": True,
        }
        chat["messages"].append(msg)
        self.note("message_sent", chat=chat_id, text=text, group=chat["kind"] == "group")
        return msg


state = SandboxState()
app = Flask(__name__)


# --------------------------------------------------------------------- шаблон
PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAX (стенд) — {{ open_chat.title if open_chat else 'Чаты' }}</title>
<style>
 :root{--bg:#0e121a;--panel:#151a24;--panel2:#1c222e;--ink:#e8ecf4;--dim:#8b95a7;
       --line:#232a37;--accent:#4b8ef7;--own:#20456f}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;display:flex;height:100vh}
 aside{width:320px;flex:0 0 320px;border-right:1px solid var(--line);
  background:var(--panel);display:flex;flex-direction:column}
 aside h1{font-size:20px;margin:0;padding:16px}
 .list{overflow:auto;flex:1}
 .row{display:flex;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line);
  text-decoration:none;color:inherit}
 .row:hover{background:var(--panel2)} .row[aria-current=true]{background:var(--panel2)}
 .av{width:40px;height:40px;flex:0 0 40px;border-radius:50%;background:var(--panel2);
  display:grid;place-items:center;font-size:13px;color:var(--dim)}
 .row b{font-weight:600;font-size:14px} .row p{margin:2px 0 0;color:var(--dim);font-size:13px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px}
 .mute{color:var(--dim);font-size:12px}
 main{flex:1;display:flex;flex-direction:column;min-width:0}
 header{padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
 header b{font-size:16px}
 .feed{flex:1;overflow:auto;padding:20px;display:flex;flex-direction:column;gap:14px}
 .msg{max-width:620px;background:var(--panel);border:1px solid var(--line);
  border-radius:14px;padding:10px 12px;position:relative}
 .msg.own{margin-left:auto;background:var(--own)}
 .msg .who{font-size:12px;color:var(--dim);margin-bottom:3px}
 .msg .rx{margin-top:6px;display:flex;gap:6px}
 .msg .rx span{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
  padding:1px 7px;font-size:13px}
 .picker{display:flex;gap:4px;margin-top:6px;opacity:.35}
 .msg:hover .picker{opacity:1}
 .picker button{background:var(--panel2);border:1px solid var(--line);color:inherit;
  border-radius:10px;padding:2px 7px;font-size:14px;cursor:pointer}
 form.composer{display:flex;gap:10px;padding:14px 20px;border-top:1px solid var(--line);
  background:var(--panel)}
 form.composer input{flex:1;background:var(--panel2);border:1px solid var(--line);
  color:inherit;border-radius:20px;padding:10px 14px}
 form.composer button{background:var(--accent);border:0;color:#fff;border-radius:20px;
  padding:10px 18px;cursor:pointer}
 .empty{margin:auto;color:var(--dim)}
 .banner{background:#3a2a10;border-bottom:1px solid #5a4418;color:#ffd79a;
  padding:8px 20px;font-size:13px}
</style></head><body>
<aside>
  <h1>Чаты <span class="mute">стенд</span></h1>
  <div class="list" data-testid="chat-list">
  {% for c in chats %}
    <a class="row" href="{{ url_for('open_chat', chat_id=c.id) }}"
       data-testid="chat-item" data-chat-id="{{ c.id }}"
       data-chat-kind="{{ c.kind }}" data-muted="{{ 'true' if c.muted else 'false' }}"
       {% if open_chat and c.id == open_chat.id %}aria-current="true"{% endif %}>
      <span class="av">{{ c.title[:2]|upper }}</span>
      <span>
        <b data-testid="chat-title">{{ c.title }}</b>
        {% if c.muted %}<span class="mute" data-testid="chat-muted" title="Уведомления отключены">без звука</span>{% endif %}
        <p>{{ c.messages[-1].text if c.messages else '' }}</p>
      </span>
    </a>
  {% endfor %}
  </div>
</aside>
<main>
{% if open_chat %}
  <header>
    <b data-testid="open-chat-title">{{ open_chat.title }}</b>
    <span class="mute" data-testid="open-chat-kind">
      {{ 'групповой чат' if open_chat.kind == 'group' else 'личный диалог' }}</span>
  </header>
  {% if open_chat.kind == 'group' %}
    <div class="banner">Это групповой чат. Бот сюда не пишет — только реакции.</div>
  {% endif %}
  <div class="feed" data-testid="message-feed">
  {% for m in open_chat.messages %}
    <div class="msg {{ 'own' if m.own else '' }}" data-testid="message"
         data-message-id="{{ m.id }}" data-own="{{ 'true' if m.own else 'false' }}"
         data-sent-at="{{ m.sent_at }}">
      <div class="who" data-testid="message-author">{{ m.author }}</div>
      <div data-testid="message-text">{{ m.text }}</div>
      {% if m.reactions %}
        <div class="rx" data-testid="message-reactions">
          {% for r in m.reactions %}<span data-testid="reaction" data-emoji="{{ r }}">{{ r }}</span>{% endfor %}
        </div>
      {% endif %}
      <form class="picker" method="post"
            action="{{ url_for('react', chat_id=open_chat.id, message_id=m.id) }}"
            data-testid="reaction-picker">
        {% for e in reaction_set %}
          <button name="emoji" value="{{ e }}" data-testid="reaction-option"
                  data-emoji="{{ e }}" title="Поставить реакцию">{{ e }}</button>
        {% endfor %}
      </form>
    </div>
  {% endfor %}
  </div>
  <form class="composer" method="post"
        action="{{ url_for('send', chat_id=open_chat.id) }}" data-testid="composer">
    <input name="text" autocomplete="off" placeholder="Сообщение"
           data-testid="composer-input" required>
    <button type="submit" data-testid="composer-send">Отправить</button>
  </form>
{% else %}
  <p class="empty">Выберите чат слева</p>
{% endif %}
</main></body></html>"""


def _render(chat_id: str | None = None):
    return render_template_string(
        PAGE,
        chats=state.chats,
        open_chat=state.chat(chat_id) if chat_id else None,
        reaction_set=REACTION_SET,
    )


# ---------------------------------------------------------------------- роуты
@app.get("/")
def index():
    return _render()


@app.get("/chat/<chat_id>")
def open_chat(chat_id: str):
    return _render(chat_id)


@app.post("/chat/<chat_id>/message/<message_id>/react")
def react(chat_id: str, message_id: str):
    emoji = request.form.get("emoji", "")
    if emoji not in REACTION_SET:
        abort(400, "неизвестная реакция")
    state.toggle_reaction(chat_id, message_id, emoji)
    return redirect(url_for("open_chat", chat_id=chat_id))


@app.post("/chat/<chat_id>/send")
def send(chat_id: str):
    text = (request.form.get("text") or "").strip()
    if not text:
        abort(400, "пустое сообщение")
    state.send(chat_id, text)
    return redirect(url_for("open_chat", chat_id=chat_id))


# ------------------------------------------------------- служебное для тестов
@app.post("/api/reset")
def api_reset():
    state.reset()
    return jsonify(ok=True, chats=len(state.chats))


@app.get("/api/audit")
def api_audit():
    """Что транспорт сделал на стенде: реакции и отправленные сообщения."""
    return jsonify(state.audit)


@app.get("/api/chats")
def api_chats():
    return jsonify([
        {"id": c["id"], "title": c["title"], "kind": c["kind"], "muted": c["muted"],
         "messages": len(c["messages"])}
        for c in state.chats
    ])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False)
