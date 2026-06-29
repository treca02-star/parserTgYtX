import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import router
from app.config import get_settings
from app.db import SessionFactory, get_session
from app.models import Source
from app.services.content import ContentPipeline
from app.services.deferred import deferred_reminder_loop
from app.services.downloader import MediaDownloader
from app.services.openai_filter import ContentAnalyzer
from app.services.substack import SubstackService
from app.services.substack_poller import (
    ensure_substack_source,
    poll_all_substack_sources,
)
from app.services.youtube import YouTubeService, parse_feed
from app.services.youtube_poller import mode_accepts, poll_all_sources

settings = get_settings()
logging.basicConfig(level=settings.log_level)
bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dispatcher = Dispatcher(storage=MemoryStorage())
dispatcher.include_router(router)
youtube = YouTubeService(settings.youtube_callback_url)
substack = SubstackService()
analyzer = ContentAnalyzer(
    settings.ai_api_key,
    settings.ai_model,
    str(settings.ai_base_url),
)
pipeline = ContentPipeline(bot, analyzer, settings)
downloader = MediaDownloader(bot, settings)
session_dependency = Depends(get_session)
polling_task: asyncio.Task[None] | None = None
deferred_reminder_task: asyncio.Task[None] | None = None
substack_polling_task: asyncio.Task[None] | None = None
telegram_polling_task: asyncio.Task[None] | None = None


class DependenciesMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):  # type: ignore[no-untyped-def]
        async with SessionFactory() as session:
            data.update(
                session=session,
                settings=settings,
                youtube=youtube,
                pipeline=pipeline,
                downloader=downloader,
            )
            return await handler(event, data)


dispatcher.update.outer_middleware(DependenciesMiddleware())


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    global deferred_reminder_task, polling_task, substack_polling_task, telegram_polling_task
    if settings.telegram_update_mode == "polling":
        await bot.delete_webhook(drop_pending_updates=False)
        telegram_polling_task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        )
    else:
        await bot.set_webhook(
            settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    async with SessionFactory() as session:
        await ensure_substack_source(
            session,
            settings.substack_feed_url,
            settings.substack_author,
        )
        sources = (
            await session.scalars(
                select(Source).where(
                    Source.kind == "youtube",
                    Source.content_mode != "off",
                )
            )
        ).all()
    for source in sources:
        try:
            await youtube.subscribe(source.external_id)
        except Exception:
            logging.exception("Could not renew YouTube subscription for %s", source.external_id)
    polling_task = asyncio.create_task(youtube_polling_loop())
    substack_polling_task = asyncio.create_task(substack_polling_loop())
    deferred_reminder_task = asyncio.create_task(
        deferred_reminder_loop(bot, SessionFactory, settings)
    )
    yield
    if polling_task:
        polling_task.cancel()
    if deferred_reminder_task:
        deferred_reminder_task.cancel()
    if substack_polling_task:
        substack_polling_task.cancel()
    if telegram_polling_task:
        telegram_polling_task.cancel()
    await substack.close()
    await bot.session.close()


async def youtube_polling_loop() -> None:
    while True:
        async with SessionFactory() as session:
            await poll_all_sources(session, youtube, pipeline)
        await asyncio.sleep(120)


async def substack_polling_loop() -> None:
    while True:
        async with SessionFactory() as session:
            await poll_all_substack_sources(session, substack, pipeline)
        await asyncio.sleep(120)


app = FastAPI(title="ParserTgYtX", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dispatcher.feed_update(bot, update)
    return {"ok": True}


@app.get("/webhooks/youtube")
async def youtube_verify(request: Request) -> Response:
    challenge = request.query_params.get("hub.challenge")
    mode = request.query_params.get("hub.mode")
    topic = request.query_params.get("hub.topic", "")
    if not challenge or mode not in {"subscribe", "unsubscribe"} or "youtube.com" not in topic:
        raise HTTPException(status_code=400, detail="Invalid WebSub verification")
    return Response(challenge, media_type="text/plain")


@app.post("/webhooks/youtube")
async def youtube_webhook(
    request: Request, session: AsyncSession = session_dependency
) -> dict[str, int]:
    payload = await request.body()
    items = parse_feed(payload)
    accepted = 0
    for item in items:
        if not item.source_external_id:
            continue
        source = await session.scalar(
            select(Source).where(
                Source.kind == "youtube",
                Source.external_id == item.source_external_id,
                Source.content_mode != "off",
            )
        )
        if not source:
            continue
        entries = await youtube.list_entries(source.url)
        kind = next(
            (entry.kind for entry in entries if entry.video_id == item.external_id),
            None,
        )
        if kind and mode_accepts(source.content_mode, kind) and await pipeline.ingest(
            session, item
        ):
            accepted += 1
    return {"accepted": accepted}
