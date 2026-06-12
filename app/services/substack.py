import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree

import httpx

from app.schemas import NormalizedItem

CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"


@dataclass(slots=True)
class SubstackEntry:
    external_id: str
    title: str
    url: str
    content: str
    published_at: datetime | None

    def normalized(self, author: str) -> NormalizedItem:
        return NormalizedItem(
            kind="substack",
            external_id=self.external_id,
            author=author,
            title_hint=self.title,
            content=self.content,
            url=self.url,
        )


def html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def parse_substack_feed(payload: bytes) -> list[SubstackEntry]:
    root = ElementTree.fromstring(payload)
    entries: list[SubstackEntry] = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        url = (node.findtext("link") or "").strip()
        external_id = (node.findtext("guid") or url).strip()
        raw_content = node.findtext(CONTENT_TAG) or node.findtext("description") or ""
        published_at = None
        if published := node.findtext("pubDate"):
            try:
                published_at = parsedate_to_datetime(published).astimezone(UTC)
            except (TypeError, ValueError):
                published_at = None
        if external_id and title and url:
            entries.append(
                SubstackEntry(
                    external_id=external_id,
                    title=title,
                    url=url,
                    content=html_to_text(raw_content),
                    published_at=published_at,
                )
            )
    return entries


class SubstackService:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            trust_env=False,
            headers={"User-Agent": "ParserTgYtX/0.1 (+RSS reader)"},
        )

    async def list_entries(self, feed_url: str) -> list[SubstackEntry]:
        response = await self.client.get(feed_url)
        response.raise_for_status()
        return parse_substack_feed(response.content)

    async def close(self) -> None:
        await self.client.aclose()
