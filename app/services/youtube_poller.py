import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, YouTubeSeen
from app.schemas import NormalizedItem
from app.services.content import ContentPipeline
from app.services.youtube import YouTubeService

logger = logging.getLogger(__name__)


def mode_accepts(mode: str, kind: str) -> bool:
    return mode == "all" or mode == kind


async def poll_source(
    session: AsyncSession,
    source: Source,
    youtube: YouTubeService,
    pipeline: ContentPipeline,
) -> int:
    entries = await youtube.list_entries(source.url)
    existing = set(
        await session.scalars(
            select(YouTubeSeen.video_id).where(YouTubeSeen.source_id == source.id)
        )
    )
    first_poll = not existing
    accepted = 0
    for entry in entries:
        if entry.video_id in existing:
            continue
        session.add(
            YouTubeSeen(source_id=source.id, video_id=entry.video_id, kind=entry.kind)
        )
        if first_poll or not mode_accepts(source.content_mode, entry.kind):
            continue
        title, is_live = await youtube.video_title(entry.video_id)
        if is_live:
            continue
        item = await pipeline.ingest(
            session,
            NormalizedItem(
                kind="youtube",
                external_id=entry.video_id,
                author=source.title,
                title_hint=title,
                content=title,
                url=f"https://www.youtube.com/watch?v={entry.video_id}",
                source_external_id=source.external_id,
            ),
        )
        accepted += int(item is not None)
    await session.commit()
    return accepted


async def poll_all_sources(
    session: AsyncSession, youtube: YouTubeService, pipeline: ContentPipeline
) -> int:
    sources = (
        await session.scalars(
            select(Source).where(
                Source.kind == "youtube",
                Source.content_mode != "off",
            )
        )
    ).all()
    accepted = 0
    for source in sources:
        try:
            accepted += await poll_source(session, source, youtube, pipeline)
        except Exception:
            await session.rollback()
            logger.exception("YouTube polling failed for source %s", source.id)
    return accepted
