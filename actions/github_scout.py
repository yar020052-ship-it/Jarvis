"""Search public GitHub repositories without downloading or executing code."""

from __future__ import annotations

import requests

_API = "https://api.github.com/search/repositories"


def github_scout(parameters: dict | None = None, **_kwargs) -> str:
    params = parameters or {}
    query = str(params.get("query") or "").strip()
    if len(query) < 2 or len(query) > 160:
        return "Укажите тему поиска на GitHub длиной от 2 до 160 символов."

    language = str(params.get("language") or "").strip()
    try:
        min_stars = int(params.get("min_stars") or 0)
    except (TypeError, ValueError):
        min_stars = 0
    min_stars = max(0, min(100000, min_stars))
    q = query
    if language:
        clean_language = "".join(ch for ch in language if ch.isalnum() or ch in "+#.- ")[:40]
        if clean_language:
            q += f" language:{clean_language}"
    if min_stars:
        q += f" stars:>={min_stars}"

    try:
        response = requests.get(
            _API,
            params={"q": q, "sort": "updated", "order": "desc", "per_page": 15},
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "JARVIS-GitHub-Scout/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=(8, 20),
        )
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            return "GitHub ограничил частоту анонимных запросов. Подождите около часа и повторите поиск."
        response.raise_for_status()
        payload = response.json()
        repos = payload.get("items") or []
        if not repos:
            return f"По запросу «{query}» подходящих публичных репозиториев не найдено."

        # The API orders by recent activity; rank this small candidate set by
        # stars while preserving recency as the tie-breaker.
        repos.sort(key=lambda item: (item.get("stargazers_count", 0), item.get("updated_at", "")), reverse=True)
        lines = [f"Кандидаты GitHub по теме «{query}» (публичные репозитории):"]
        for repo in repos[:7]:
            description = (repo.get("description") or "Без описания").replace("\n", " ").strip()
            lines.append(
                f"- {repo.get('full_name')}: ★ {repo.get('stargazers_count', 0)}, "
                f"последнее обновление {str(repo.get('updated_at', ''))[:10]}. "
                f"{description[:260]}\n  {repo.get('html_url', '')}"
            )
        lines.append(
            "Это первичный список: звёзды не подтверждают качество или безопасность. "
            "Перед переносом функции нужно изучить код, активность и зависимости. "
            "Инструмент не скачивает и не запускает репозитории."
        )
        return "\n".join(lines)
    except requests.RequestException as exc:
        return f"Не удалось выполнить поиск GitHub: {exc}"
    except (ValueError, TypeError) as exc:
        return f"GitHub вернул некорректный ответ: {exc}"


TOOL = {
    "name": "github_scout",
    "description": (
        "Searches GitHub's public repository index by topic, optionally filtering by "
        "programming language and minimum stars. Returns repository links, recent "
        "activity and star counts. Use this to find open "
        "source projects and possible features for JARVIS. This is discovery only: "
        "never claim a repository is safe based on stars, and never download or run code. "
        "For a promising result, call github_integrate with owner/name to read its README "
        "and key files, then self_integrate to install a reviewed file."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "Search topic, such as Gemini Live desktop assistant"},
            "language": {"type": "STRING", "description": "Optional GitHub language filter, such as Python"},
            "min_stars": {"type": "INTEGER", "description": "Optional minimum GitHub stars"},
        },
        "required": ["query"],
    },
    "handler": github_scout,
}
