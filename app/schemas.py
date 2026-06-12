from dataclasses import dataclass


@dataclass(slots=True)
class NormalizedItem:
    kind: str
    external_id: str
    author: str
    content: str
    url: str
    title_hint: str = ""
    media_type: str = "none"
    source_external_id: str | None = None
    source_chat_id: int | None = None
    source_message_id: int | None = None


@dataclass(slots=True)
class AnalysisResult:
    relevant: bool
    score: float
    title: str
    summary: str
    is_ad: bool = False
