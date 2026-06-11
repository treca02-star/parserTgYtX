from app.bot.handlers import allowed
from app.config import get_settings
from app.models import ContentItem
from app.schemas import NormalizedItem
from app.services.content import format_card, item_keyboard
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
    assert "Передано в обработку" in format_card(item, sent=True)
