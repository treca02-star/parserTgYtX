from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import Base
from app.schemas import AnalysisResult, NormalizedItem
from app.services.content import ContentPipeline


class FakeAnalyzer:
    async def analyze(
        self, item: NormalizedItem, mode: str, custom_prompt: str
    ) -> AnalysisResult:
        return AnalysisResult(
            True,
            0.9,
            item.title_hint or "Тема",
            "Краткое описание",
            category="Анализ рынка",
        )


class FakeAdAnalyzer:
    async def analyze(
        self, item: NormalizedItem, mode: str, custom_prompt: str
    ) -> AnalysisResult:
        return AnalysisResult(True, 0.1, "Реклама", "Описание", True)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_ingest_deduplicates_items(session_factory) -> None:
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=100)
    pipeline = ContentPipeline(bot, FakeAnalyzer(), get_settings())  # type: ignore[arg-type]
    incoming = NormalizedItem(
        kind="youtube",
        external_id="video-1",
        author="Author",
        title_hint="Title",
        content="Content",
        url="https://youtube.test/watch?v=video-1",
    )

    async with session_factory() as session:
        first = await pipeline.ingest(session, incoming)
        duplicate = await pipeline.ingest(session, incoming)

    assert first is not None
    assert duplicate is None
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_is_idempotent(session_factory) -> None:
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=100)
    pipeline = ContentPipeline(bot, FakeAnalyzer(), get_settings())  # type: ignore[arg-type]
    incoming = NormalizedItem(
        kind="telegram",
        external_id="-1001:10",
        author="Channel",
        title_hint="Title",
        content="Content",
        url="https://t.me/channel/10",
        source_chat_id=-1001,
        source_message_id=10,
    )

    async with session_factory() as session:
        item = await pipeline.ingest(session, incoming)
        assert item is not None
        _, first = await pipeline.publish(session, item.id)
        _, second = await pipeline.publish(session, item.id)

    assert first is True
    assert second is False
    bot.copy_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_ad_is_delivered_as_compact_card(session_factory) -> None:
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=101)
    pipeline = ContentPipeline(bot, FakeAdAnalyzer(), get_settings())  # type: ignore[arg-type]
    incoming = NormalizedItem(
        kind="telegram",
        external_id="-1001:11",
        author="#Канал",
        content="Покупайте VPN по промокоду.",
        url="https://t.me/channel/11",
    )

    async with session_factory() as session:
        item = await pipeline.ingest(session, incoming)

    assert item is not None
    assert item.is_ad is True
    assert bot.send_message.await_args.args[1] == "<b>#Канал | Рекламный пост</b>"
