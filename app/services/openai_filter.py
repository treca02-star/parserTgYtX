import json
import re

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
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты редактор русскоязычного криптоканала. Оцени, насколько материал "
                        "соответствует критериям пользователя: score=1 означает полное "
                        "соответствие, score=0 — отсутствие соответствия. Не снижай оценку "
                        "из-за неопределенности прогноза или отсутствия доказательств: оценивай "
                        "тему и содержание, а не достоверность инвестиционного тезиса. "
                        "Кратко опиши, о чем материал. Если переданы сведения о вложениях или "
                        "YouTube-ссылках, обязательно упомяни их в summary, но не утверждай, "
                        "что знаешь содержание непрочитанного медиа. Верни только JSON: "
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
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.1,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("AI provider returned an empty response")
        data = json.loads(content)
        score = max(0.0, min(1.0, float(data["score"])))
        return AnalysisResult(
            relevant=mode == "all" or score >= THRESHOLDS[mode],
            score=score,
            title=str(data["title"])[:255],
            summary=str(data["summary"])[:1000],
        )

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
    def _fallback_title(item: NormalizedItem) -> str:
        return (item.title_hint or item.content or "Новый материал")[:80]
