import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, SubstackSeen
from app.services.content import ContentPipeline
from app.services.substack import SubstackService

logger = logging.getLogger(__name__)
MAX_ENTRY_AGE = timedelta(days=3)


async def ensure_substack_source(
    session: AsyncSession,
    feed_url: str,
    author: str,
) -> Source | None:
    if not feed_url:
        return None
    source = await session.scalar(
        select(Source).where(Source.kind == "substack", Source.external_id == feed_url)
    )
    if source is None:
        source = Source(
            kind="substack",
            external_id=feed_url,
            title=author,
            url=feed_url,
            enabled=True,
            content_mode="all",
        )
        session.add(source)
        await session.commit()
        await session.refresh(source)
    return source


async def poll_substack_source(
    session: AsyncSession,
    source: Source,
    substack: SubstackService,
    pipeline: ContentPipeline,
) -> int:
    entries = await substack.list_entries(source.url)
    existing = set(
        await session.scalars(
            select(SubstackSeen.external_id).where(SubstackSeen.source_id == source.id)
        )
    )
    first_poll = not existing
    accepted = 0
    now = datetime.now(UTC)
    for entry in entries:
        if entry.external_id in existing:
            continue
        session.add(SubstackSeen(source_id=source.id, external_id=entry.external_id))
        if (
            first_poll
            or entry.published_at is None
            or now - entry.published_at > MAX_ENTRY_AGE
        ):
            continue
        item = await pipeline.ingest(session, entry.normalized(source.title))
        accepted += int(item is not None)
    await session.commit()
    return accepted


async def poll_all_substack_sources(
    session: AsyncSession,
    substack: SubstackService,
    pipeline: ContentPipeline,
) -> int:
    sources = (
        await session.scalars(
            select(Source).where(
                Source.kind == "substack",
                Source.enabled.is_(True),
            )
        )
    ).all()
    accepted = 0
    for source in sources:
        try:
            accepted += await poll_substack_source(session, source, substack, pipeline)
        except Exception:
            await session.rollback()
            logger.exception("Substack polling failed for source %s", source.id)
    return accepted
