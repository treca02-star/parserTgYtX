from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import process_inbox_message, telegram_message_url
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
