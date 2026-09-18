"""База знаний: markdown-статьи с frontmatter, без внешних зависимостей.

Статья — это одновременно инструкция для человека и данные для бота: из
frontmatter берутся категории, требуемые сущности (`needs`), предлагаемое
решение и имя скрипта, а из тела — готовый ответ пользователю.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ANSWER = re.compile(r"\*\*Ответ пользователю:\*\*\s*\n(.*?)(?=\n\*\*|\Z)", re.DOTALL)
_QUESTIONS = re.compile(r"\*\*Уточняющие вопросы:\*\*\s*(.*?)(?=\n\*\*|\Z)", re.DOTALL)


def _parse_scalar(raw: str) -> str | list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [x.strip() for x in inner.split(",") if x.strip()] if inner else []
    return raw.strip("\"'")


@dataclass(slots=True)
class Article:
    slug: str
    title: str
    categories: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    resolution: str = "need_clarify"
    script: str | None = None
    body: str = ""

    @property
    def answer(self) -> str:
        """Готовый текст ответа пользователю (без markdown-разметки)."""
        m = _ANSWER.search(self.body)
        if not m:
            return ""
        text = re.sub(r"\s*\n\s*", " ", m.group(1)).strip()
        return re.sub(r"\s{2,}", " ", text)

    @property
    def clarifying_questions(self) -> str:
        m = _QUESTIONS.search(self.body)
        return re.sub(r"\s*\n\s*", " ", m.group(1)).strip() if m else ""


class KnowledgeBase:
    """Загружает и отдаёт статьи. Перечитывает файлы при изменении на диске."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._articles: dict[str, Article] = {}
        self._mtime = 0.0
        self.reload()

    def reload(self) -> None:
        articles: dict[str, Article] = {}
        if not self.directory.is_dir():
            self._articles = articles
            return
        for path in sorted(self.directory.glob("*.md")):
            article = self._parse(path)
            if article:
                articles[article.slug] = article
        self._articles = articles
        self._mtime = self._dir_mtime()

    def _dir_mtime(self) -> float:
        if not self.directory.is_dir():
            return 0.0
        return max((p.stat().st_mtime for p in self.directory.glob("*.md")), default=0.0)

    def _maybe_reload(self) -> None:
        if self._dir_mtime() > self._mtime:
            self.reload()

    @staticmethod
    def _parse(path: Path) -> Article | None:
        text = path.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            return None
        meta: dict[str, str | list[str]] = {}
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip()] = _parse_scalar(value)
        slug = meta.get("slug") or path.stem
        cats = meta.get("categories") or []
        needs = meta.get("needs") or []
        return Article(
            slug=str(slug),
            title=str(meta.get("title") or path.stem),
            categories=cats if isinstance(cats, list) else [str(cats)],
            needs=needs if isinstance(needs, list) else [str(needs)],
            resolution=str(meta.get("resolution") or "need_clarify"),
            script=str(meta["script"]) if meta.get("script") else None,
            body=text[m.end():],
        )

    # ------------------------------------------------------------------ API
    def all(self) -> list[Article]:
        self._maybe_reload()
        return list(self._articles.values())

    def get(self, slug: str) -> Article | None:
        self._maybe_reload()
        return self._articles.get(slug)

    def for_category(self, category: str) -> list[Article]:
        """Статьи, подходящие категории. Более специфичные (меньше категорий) — первыми."""
        self._maybe_reload()
        hits = [a for a in self._articles.values() if category in a.categories]
        return sorted(hits, key=lambda a: (len(a.categories), a.slug))
