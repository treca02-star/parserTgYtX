from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.services.youtube import YouTubeService
from app.services.youtube_poller import MAX_POLL_VIDEO_AGE, mode_accepts


@pytest.mark.asyncio
@respx.mock
async def test_resolve_handle_from_browse_id() -> None:
    route = respx.get("https://www.youtube.com/@HAMAHA-bitcoin").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<html><head><title>HAMAHA Bitcoin - YouTube</title></head>"
                '<body>{"browseId":"UCI3uVtN-W5StRN1RsLNKV6g"}</body></html>'
            ),
        )
    )

    channel = await YouTubeService("https://example.test/webhooks/youtube").resolve_channel(
        "https://www.youtube.com/@HAMAHA-bitcoin"
    )

    assert route.called
    assert channel.channel_id == "UCI3uVtN-W5StRN1RsLNKV6g"
    assert channel.title == "HAMAHA Bitcoin"


def test_source_modes() -> None:
    assert mode_accepts("all", "long")
    assert mode_accepts("all", "shorts")
    assert mode_accepts("long", "long")
    assert not mode_accepts("long", "shorts")
    assert mode_accepts("shorts", "shorts")
    assert not mode_accepts("off", "long")


@pytest.mark.asyncio
@respx.mock
async def test_video_details_reads_publish_date() -> None:
    respx.get("https://www.youtube.com/watch?v=video123456").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<html><head><title>Market update - YouTube</title></head>"
                '<body>{"publishDate":"2026-06-11","isLiveContent":false}</body></html>'
            ),
        )
    )

    title, is_live, published_at = await YouTubeService(
        "https://example.test/webhooks/youtube"
    ).video_details("video123456")

    assert title == "Market update"
    assert is_live is False
    assert published_at == datetime(2026, 6, 11, tzinfo=UTC)
    assert MAX_POLL_VIDEO_AGE == timedelta(hours=48)
