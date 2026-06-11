from unittest.mock import AsyncMock, patch

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


def test_youtube_download_uses_token_provider_and_javascript_runtime(tmp_path) -> None:
    info = {"id": "video", "ext": "mp4"}
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    with patch("app.services.downloader.yt_dlp.YoutubeDL") as youtube_dl:
        instance = youtube_dl.return_value.__enter__.return_value
        instance.extract_info.return_value = info
        instance.prepare_filename.return_value = str(video)

        result = MediaDownloader._download_youtube(
            "https://www.youtube.com/watch?v=video",
            tmp_path,
        )

    options = youtube_dl.call_args.args[0]
    assert options["js_runtimes"] == {"node": {}}
    assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
        "http://pot-provider:4416"
    ]
    assert result == video
