from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.handlers import allowed, answer_callback_safely, edit_callback_safely
from app.config import get_settings
from app.models import ContentItem
from app.schemas import NormalizedItem
from app.services.content import format_card, item_keyboard, media_notes
from app.services.openai_filter import THRESHOLDS, ContentAnalyzer
from app.services.youtube import parse_feed


def test_owner_access() -> None:
    settings = get_settings()
    assert allowed(42, settings)
    assert not allowed(41, settings)


@pytest.mark.asyncio
async def test_stale_callback_answer_is_ignored() -> None:
    callback = AsyncMock()
    callback.answer.side_effect = TelegramBadRequest(
        method=AsyncMock(),
        message="query is too old and response timeout expired",
    )

    await answer_callback_safely(callback, "Передаю")


@pytest.mark.asyncio
async def test_unchanged_callback_message_is_ignored() -> None:
    message = AsyncMock()
    message.edit_text.side_effect = TelegramBadRequest(
        method=AsyncMock(),
        message="message is not modified",
    )
    callback = AsyncMock()
    callback.message = message

    with patch("app.bot.handlers.callback_message", return_value=message):
        await edit_callback_safely(callback, "Текст", item_keyboard(1, "https://t.me/test"))


def test_filter_thresholds_are_ordered() -> None:
    assert THRESHOLDS["all"] < THRESHOLDS["soft"] < THRESHOLDS["medium"] < THRESHOLDS["strict"]


def test_ai_media_context_describes_attachments_and_youtube_links() -> None:
    item = NormalizedItem(
        kind="telegram",
        external_id="post-1",
        author="Author",
        content="Подробности: https://youtu.be/example",
        media_type="video",
        url="https://t.me/example/1",
    )

    context = ContentAnalyzer._media_context(item)

    assert "видео Telegram" in context
    assert "ссылка YouTube" in context


def test_ai_response_accepts_single_object_array() -> None:
    result = ContentAnalyzer._decode_response(
        '[{"score": 0.8, "title": "Тема", "category": "Анализ BTC", '
        '"summary": "Описание", '
        '"is_ad": false, "ad_confidence": 0.1}]'
    )

    assert result["score"] == 0.8
    assert result["title"] == "Тема"


def test_ai_response_requires_ad_confidence() -> None:
    try:
        ContentAnalyzer._decode_response(
            '{"score": 0.8, "title": "Тема", "category": "Реклама", '
            '"summary": "Описание", "is_ad": true}'
        )
    except KeyError as error:
        assert error.args == ("ad_confidence",)
    else:
        raise AssertionError("ad_confidence must be required")


def test_ad_requires_high_confidence() -> None:
    assert ContentAnalyzer._is_confident_ad({"is_ad": True, "ad_confidence": 0.9})
    assert not ContentAnalyzer._is_confident_ad({"is_ad": True, "ad_confidence": 0.89})
    assert not ContentAnalyzer._is_confident_ad({"is_ad": False, "ad_confidence": 1})


def test_category_is_short_and_ads_are_forced_to_advertising() -> None:
    assert ContentAnalyzer._clean_category("  Сделка   на   ETH  ") == "Сделка на ETH"
    assert ContentAnalyzer._clean_category("Очень длинная категория из многих слов") == (
        "Очень длинная категория"
    )
    assert ContentAnalyzer._clean_category("Анализ BTC", is_ad=True) == "Реклама"


def test_ai_title_removes_repeated_author() -> None:
    assert (
        ContentAnalyzer._clean_title(
            "Обзор рыночных трендов от #Trader8020",
            "#Trader8020",
        )
        == "Обзор рыночных трендов"
    )


def test_youtube_feed_normalization() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <title>Crypto Author</title>
      <entry>
        <yt:videoId>abc123</yt:videoId>
        <yt:channelId>UCtest</yt:channelId>
        <title>Market update</title>
      </entry>
    </feed>"""
    items = parse_feed(payload)
    assert len(items) == 1
    assert items[0].external_id == "abc123"
    assert items[0].author == "Crypto Author"
    assert items[0].source_external_id == "UCtest"
    assert items[0].url.endswith("abc123")


def test_sent_card_keeps_only_link_button() -> None:
    item = ContentItem(
        id=7,
        kind="youtube",
        external_id="abc",
        author="Author",
        title="Title",
        category="Видеообзор",
        summary="Summary",
        content="",
        url="https://youtube.com/watch?v=abc",
        relevance=0.9,
        status="sent",
    )
    keyboard = item_keyboard(item.id, item.url, "video", sent=True)
    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].url == item.url
    assert keyboard.inline_keyboard[0][1].callback_data == "download:7"
    card = format_card(item, sent=True)
    assert '<a href="https://youtube.com/watch?v=abc">Источник</a>' in card
    assert "Видеообзор" in card
    assert "Релевантность" not in card
    assert "Передано в обработку" in card


def test_processing_card_shows_progress_and_disables_publish() -> None:
    item = ContentItem(
        id=10,
        kind="telegram",
        external_id="processing",
        author="#Канал",
        title="Прогноз BTC",
        category="Прогноз BTC",
        summary="Описание",
        content="",
        url="https://t.me/source/10",
        relevance=1,
        status="new",
    )

    card = format_card(item, processing=True)
    keyboard = item_keyboard(
        item.id,
        item.url,
        item.media_type,
        processing=True,
    )

    assert "■■■□□ 60%" in card
    assert keyboard.inline_keyboard[0][0].text == "⏳ Передаю…"
    assert keyboard.inline_keyboard[0][0].callback_data == "publish:10"
    assert keyboard.inline_keyboard[0][1].url == item.url


def test_card_uses_short_media_notes() -> None:
    item = ContentItem(
        id=8,
        kind="telegram",
        external_id="post",
        author="#Trader8020",
        title="Обзор рынка",
        category="Анализ рынка",
        summary="Краткое описание.",
        content="",
        media_type="voice",
        url="https://t.me/source/1",
        relevance=1,
        status="new",
    )

    assert media_notes(item) == "🎙 Голосовое сообщение"
    card = format_card(item)
    assert "#Trader8020 | Обзор рынка | Пост TG" in card
    assert "Анализ рынка" in card
    assert "Релевантность" not in card
    assert "прикреплено голосовое" not in card


def test_ad_card_is_one_line() -> None:
    item = ContentItem(
        id=9,
        kind="telegram",
        external_id="ad",
        author="#Канал",
        title="Реклама VPN",
        category="Реклама",
        summary="Большое рекламное описание.",
        content="",
        media_type="none",
        is_ad=True,
        url="https://t.me/source/2",
        relevance=0.1,
        status="new",
    )

    assert format_card(item) == "<b>#Канал | Рекламный пост</b>"
