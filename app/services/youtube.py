import re
from dataclasses import dataclass
from html import unescape
from xml.etree import ElementTree

import httpx

from app.schemas import NormalizedItem

HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
CHANNEL_PATTERNS = (
    re.compile(r"youtube\.com/channel/(?P<id>UC[\w-]+)"),
    re.compile(r"youtube\.com/@(?P<handle>[\w.-]+)"),
)
CHANNEL_ID_PATTERNS = (
    re.compile(r'"channelId":"(UC[\w-]+)"'),
    re.compile(r'"browseId":"(UC[\w-]+)"'),
    re.compile(r'<meta itemprop="channelId" content="(UC[\w-]+)"'),
    re.compile(r"youtube\.com/channel/(UC[\w-]+)"),
)
YOUTUBE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "SOCS=CAI",
}


@dataclass(slots=True)
class YouTubeChannel:
    channel_id: str
    title: str
    url: str


class YouTubeService:
    def __init__(self, callback_url: str) -> None:
        self.callback_url = callback_url

    async def resolve_channel(self, value: str) -> YouTubeChannel:
        value = value.strip()
        direct = CHANNEL_PATTERNS[0].search(value)
        if direct:
            channel_id = direct.group("id")
            return YouTubeChannel(channel_id, channel_id, f"https://youtube.com/channel/{channel_id}")
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15, trust_env=False
        ) as client:
            response = await client.get(value, headers=YOUTUBE_HEADERS)
            response.raise_for_status()
        channel_id = next(
            (
                match.group(1)
                for pattern in CHANNEL_ID_PATTERNS
                if (match := pattern.search(response.text))
            ),
            None,
        )
        title = re.search(r"<title>(.*?)</title>", response.text, re.IGNORECASE)
        if not channel_id:
            raise ValueError("Не удалось определить ID YouTube-канала")
        return YouTubeChannel(
            channel_id,
            (
                unescape(title.group(1)).replace(" - YouTube", "")
                if title
                else channel_id
            ),
            str(response.url),
        )

    async def subscribe(self, channel_id: str, mode: str = "subscribe") -> None:
        topic = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(
                HUB_URL,
                data={
                    "hub.callback": self.callback_url,
                    "hub.topic": topic,
                    "hub.verify": "async",
                    "hub.mode": mode,
                    "hub.lease_seconds": "864000",
                },
            )
            response.raise_for_status()


def parse_feed(payload: bytes) -> list[NormalizedItem]:
    root = ElementTree.fromstring(payload)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    items: list[NormalizedItem] = []
    author = root.findtext("atom:title", default="YouTube", namespaces=ns)
    for entry in root.findall("atom:entry", ns):
        video_id = entry.findtext("yt:videoId", namespaces=ns)
        channel_id = entry.findtext("yt:channelId", namespaces=ns)
        if not video_id:
            continue
        title = entry.findtext("atom:title", default="Новое видео", namespaces=ns)
        items.append(
            NormalizedItem(
                kind="youtube",
                external_id=video_id,
                author=author,
                title_hint=title,
                source_external_id=channel_id,
                content=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
            )
        )
    return items
