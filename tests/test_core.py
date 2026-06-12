from app.bot.handlers import allowed
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
        '[{"score": 0.8, "title": "Тема", "summary": "Описание"}]'
    )

    assert result["score"] == 0.8
    assert result["title"] == "Тема"


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
    assert card.index("Источник") < card.index("Релевантность")
    assert "Передано в обработку" in card


def test_card_uses_short_media_notes() -> None:
    item = ContentItem(
        id=8,
        kind="telegram",
        external_id="post",
        author="#Trader8020",
        title="Обзор рынка",
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
    assert "прикреплено голосовое" not in card
