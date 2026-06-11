import json
import re
from typing import Any, cast

import httpx
from openai import AsyncOpenAI

from app.schemas import AnalysisResult, NormalizedItem

THRESHOLDS = {"all": 0.0, "soft": 0.35, "medium": 0.6, "strict": 0.8}


class ContentAnalyzer:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(trust_env=False),
        )
        self.model = model

    async def analyze(
        self, item: NormalizedItem, mode: str, custom_prompt: str
    ) -> AnalysisResult:
        media_context = self._media_context(item)
        messages = [
                {
                    "role": "system",
                    "content": (
                        "Ты редактор русскоязычного криптоканала. Оцени, насколько материал "
                        "соответствует критериям пользователя: score=1 означает полное "
                        "соответствие, score=0 — отсутствие соответствия. Не снижай оценку "
                        "из-за неопределенности прогноза или отсутствия доказательств: оценивай "
                        "тему и содержание, а не достоверность инвестиционного тезиса. "
                        "Кратко опиши, о чем материал. Если переданы сведения о вложениях или "
                        "YouTube-ссылках, используй их только для понимания контекста и оценки. "
                        "Не упоминай вложения, ссылки и дополнительные материалы в summary: "
                        "бот добавит их отдельно. В title никогда не пиши автора, название "
                        "канала, хештег автора или конструкцию «от автора». Верни только JSON: "
                        "score (0..1), title (до 80 символов), summary (до 300 символов). "
                        "Пиши по-русски и не давай финансовых обещаний."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Критерии: {custom_prompt}\n"
                        f"Источник: {item.kind}\n"
                        f"Автор: {item.author}\n"
                        f"Технические сведения: {media_context}\n"
                        f"Текст материала:\n{item.content[:12000]}"
                    ),
                },
            ]
        data = None
        last_error: Exception | None = None
        for _ in range(2):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=cast(Any, messages),
                response_format=cast(
                    Any,
                    {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "content_analysis",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "score": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "title": {"type": "string"},
                                    "summary": {"type": "string"},
                                },
                                "required": ["score", "title", "summary"],
                                "additionalProperties": False,
                            },
                        },
                    },
                ),
                max_tokens=300,
                temperature=0.1,
            )
            try:
                data = self._decode_response(response.choices[0].message.content)
                break
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
                last_error = error
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Предыдущий ответ имел неверный формат. "
                            "Верни один JSON-объект по схеме."
                        ),
                    }
                )
        if data is None:
            raise ValueError("AI provider returned invalid JSON") from last_error
        score = max(0.0, min(1.0, float(str(data["score"]))))
        return AnalysisResult(
            relevant=mode == "all" or score >= THRESHOLDS[mode],
            score=score,
            title=self._clean_title(str(data["title"]), item.author),
            summary=str(data["summary"])[:1000],
        )

    @staticmethod
    def _decode_response(content: str | None) -> dict[str, Any]:
        if not content:
            raise ValueError("AI provider returned an empty response")
        data = json.loads(content)
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise TypeError("AI response must be a JSON object")
        for field in ("score", "title", "summary"):
            if field not in data:
                raise KeyError(field)
        return data

    @staticmethod
    def _media_context(item: NormalizedItem) -> str:
        details = []
        if item.kind == "youtube":
            details.append("источник является видео YouTube")
        elif item.media_type == "video":
            details.append("к сообщению прикреплено видео Telegram")
        elif item.media_type == "audio":
            details.append("к сообщению прикреплено аудио или голосовое сообщение Telegram")
        youtube_links = re.findall(
            r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+",
            item.content,
            flags=re.IGNORECASE,
        )
        if youtube_links and item.kind != "youtube":
            details.append(f"в тексте найдена ссылка YouTube: {youtube_links[0]}")
        return "; ".join(details) if details else "медиа и YouTube-ссылки не обнаружены"

    @staticmethod
    def _clean_title(title: str, author: str) -> str:
        cleaned = title.strip()
        author_pattern = re.escape(author.strip())
        cleaned = re.sub(
            rf"\s+от\s+{author_pattern}\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(author_pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip(" |—–-:")[:255] or "Новый материал"

    @staticmethod
    def _fallback_title(item: NormalizedItem) -> str:
        return (item.title_hint or item.content or "Новый материал")[:80]
