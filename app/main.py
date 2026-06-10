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
from app.services.openai_filter import ContentAnalyzer
from app.services.youtube import YouTubeService, parse_feed

settings = get_settings()
logging.basicConfig(level=settings.log_level)
bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dispatcher = Dispatcher(storage=MemoryStorage())
dispatcher.include_router(router)
youtube = YouTubeService(settings.youtube_callback_url)
analyzer = ContentAnalyzer(settings.openai_api_key, settings.openai_model)
pipeline = ContentPipeline(bot, analyzer, settings)
session_dependency = Depends(get_session)


class DependenciesMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):  # type: ignore[no-untyped-def]
        async with SessionFactory() as session:
            data.update(
                session=session,
                settings=settings,
                youtube=youtube,
                pipeline=pipeline,
            )
            return await handler(event, data)


dispatcher.update.outer_middleware(DependenciesMiddleware())


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    await bot.set_webhook(
        settings.telegram_webhook_url,
        secret_token=settings.telegram_webhook_secret,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )
    async with SessionFactory() as session:
        sources = (
            await session.scalars(select(Source).where(Source.kind == "youtube", Source.enabled))
        ).all()
    for source in sources:
        try:
            await youtube.subscribe(source.external_id)
        except Exception:
            logging.exception("Could not renew YouTube subscription for %s", source.external_id)
    yield
    await bot.session.close()


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
                Source.enabled,
            )
        )
        if source and await pipeline.ingest(session, item):
            accepted += 1
    return {"accepted": accepted}
