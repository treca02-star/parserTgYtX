from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Source, SubstackSeen
from app.services.substack import SubstackEntry, html_to_text, parse_substack_feed
from app.services.substack_poller import poll_substack_source


def test_parse_substack_feed_extracts_article() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
      <channel>
        <item>
          <title>Market essay</title>
          <link>https://example.substack.com/p/market-essay</link>
          <guid>post-123</guid>
          <pubDate>Thu, 11 Jun 2026 10:00:00 GMT</pubDate>
          <content:encoded>
            <![CDATA[<p>Bitcoin &amp; liquidity.</p><p>Second point.</p>]]>
          </content:encoded>
        </item>
      </channel>
    </rss>"""

    entries = parse_substack_feed(payload)

    assert len(entries) == 1
    assert entries[0].external_id == "post-123"
    assert entries[0].title == "Market essay"
    assert entries[0].content == "Bitcoin & liquidity.\nSecond point."
    assert entries[0].published_at == datetime(2026, 6, 11, 10, tzinfo=UTC)


def test_html_to_text_removes_markup_and_scripts() -> None:
    assert html_to_text("<h2>Title</h2><script>bad()</script><p>Text&nbsp;here</p>") == (
        "Title\nText here"
    )


@pytest.mark.asyncio
async def test_first_poll_baselines_old_entries_and_next_poll_ingests_new() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source = Source(
        kind="substack",
        external_id="https://example.substack.com/feed",
        title="#Arthur_Hayes",
        url="https://example.substack.com/feed",
        enabled=True,
    )
    old_entry = SubstackEntry(
        external_id="old",
        title="Old essay",
        url="https://example.substack.com/p/old",
        content="Old",
        published_at=datetime.now(UTC),
    )
    new_entry = SubstackEntry(
        external_id="new",
        title="New essay",
        url="https://example.substack.com/p/new",
        content="New",
        published_at=datetime.now(UTC),
    )
    substack = AsyncMock()
    pipeline = AsyncMock()

    async with factory() as session:
        session.add(source)
        await session.commit()
        await session.refresh(source)

        substack.list_entries.return_value = [old_entry]
        assert await poll_substack_source(session, source, substack, pipeline) == 0
        pipeline.ingest.assert_not_awaited()

        substack.list_entries.return_value = [new_entry, old_entry]
        pipeline.ingest.return_value = object()
        assert await poll_substack_source(session, source, substack, pipeline) == 1
        pipeline.ingest.assert_awaited_once()
        assert len((await session.scalars(SubstackSeen.__table__.select())).all()) == 2

    await engine.dispose()
