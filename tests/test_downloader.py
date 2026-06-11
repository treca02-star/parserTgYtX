from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.models import ContentItem
from app.services.downloader import MediaDownloader


@pytest.mark.asyncio
async def test_telegram_video_is_copied_to_owner() -> None:
    bot = AsyncMock()
    downloader = MediaDownloader(bot, get_settings())
    item = ContentItem(
        id=1,
        kind="telegram",
        external_id="-1001:5",
        author="Channel",
        title="Video",
        summary="",
        content="",
        media_type="video",
        url="https://t.me/c/1/5",
        source_chat_id=-1001,
        source_message_id=5,
        relevance=1.0,
        status="new",
    )

    await downloader.send(item)

    bot.copy_message.assert_awaited_once_with(42, -1001, 5)
