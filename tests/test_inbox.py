from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from app.bot.handlers import parse_inbox_content, process_inbox_message, telegram_message_url
from app.config import get_settings


@pytest.mark.asyncio
async def test_group_message_is_ingested() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-1001, username=None, title="Parser", type="group"),
        text="Важное мнение о рынке",
        caption=None,
        message_id=15,
        author_signature=None,
        bot=AsyncMock(),
        video=None,
        video_note=None,
        document=None,
        audio=None,
        voice=None,
        entities=None,
        caption_entities=None,
    )
    message.bot.get_chat.return_value = SimpleNamespace(
        invite_link="https://t.me/+private-group"
    )
    pipeline = AsyncMock()

    await process_inbox_message(
        message,
        pipeline,
        AsyncMock(),
        get_settings(),
    )

    pipeline.ingest.assert_awaited_once()
    incoming = pipeline.ingest.await_args.args[1]
    assert incoming.kind == "telegram"
    assert incoming.external_id == "-1001:15"
    assert incoming.url == "https://t.me/+private-group"


def test_private_supergroup_message_url() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-1001234567890, username=None, type="supergroup"),
        message_id=42,
    )
    assert telegram_message_url(message) == "https://t.me/c/1234567890/42"


@pytest.mark.asyncio
async def test_telegram_audio_is_reported_to_pipeline() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-1001, username=None, title="Parser", type="group"),
        text=None,
        caption="Аудиокомментарий о рынке",
        message_id=16,
        author_signature=None,
        bot=AsyncMock(),
        video=None,
        video_note=None,
        document=None,
        audio=SimpleNamespace(),
        voice=None,
        entities=None,
        caption_entities=None,
    )
    message.bot.get_chat.return_value = SimpleNamespace(
        invite_link="https://t.me/+private-group"
    )
    pipeline = AsyncMock()

    await process_inbox_message(message, pipeline, AsyncMock(), get_settings())

    incoming = pipeline.ingest.await_args.args[1]
    assert incoming.media_type == "audio"


def test_inbox_metadata_is_extracted_from_name_and_source_link() -> None:
    text = "Имя: #Слезы_Сатоши\n\nРазбор движения Bitcoin.\n\nИсточник"
    source_offset = len(text) - len("Источник")
    message = Message.model_validate(
        {
            "message_id": 17,
            "date": 0,
            "chat": {"id": -1001, "type": "supergroup", "title": "Parser"},
            "text": text,
            "entities": [
                {
                    "type": "text_link",
                    "offset": source_offset,
                    "length": len("Источник"),
                    "url": "https://t.me/source/123",
                }
            ],
        }
    )

    author, content, source_url = parse_inbox_content(message)

    assert author == "#Слезы_Сатоши"
    assert content == "Разбор движения Bitcoin."
    assert source_url == "https://t.me/source/123"
