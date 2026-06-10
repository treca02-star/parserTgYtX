import json

import httpx
from openai import AsyncOpenAI

from app.schemas import AnalysisResult, NormalizedItem

THRESHOLDS = {"all": 0.0, "soft": 0.35, "medium": 0.6, "strict": 0.8}


class ContentAnalyzer:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            http_client=httpx.AsyncClient(trust_env=False),
        )
        self.model = model

    async def analyze(
        self, item: NormalizedItem, mode: str, custom_prompt: str
    ) -> AnalysisResult:
        if mode == "all":
            return AnalysisResult(True, 1.0, self._fallback_title(item), "Без AI-фильтра")
        response = await self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты редактор русского крипто-канала. Оцени полезность материала. "
                        "Верни только JSON: score (0..1), title (до 80 символов), "
                        "summary (до 240 символов). Не давай финансовых обещаний."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Критерии: {custom_prompt}\n"
                        f"Автор: {item.author}\n{item.content[:12000]}"
                    ),
                },
            ],
        )
        data = json.loads(response.output_text)
        score = max(0.0, min(1.0, float(data["score"])))
        return AnalysisResult(
            relevant=score >= THRESHOLDS[mode],
            score=score,
            title=str(data["title"])[:255],
            summary=str(data["summary"])[:1000],
        )

    @staticmethod
    def _fallback_title(item: NormalizedItem) -> str:
        return (item.title_hint or item.content or "Новый материал")[:80]
