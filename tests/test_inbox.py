from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import process_inbox_message
from app.config import get_settings


@pytest.mark.asyncio
async def test_group_message_is_ingested() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-1001, username=None, title="Parser"),
        text="Важное мнение о рынке",
        caption=None,
        message_id=15,
        author_signature=None,
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
