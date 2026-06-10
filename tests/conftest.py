import os

os.environ.update(
    {
        "TELEGRAM_BOT_TOKEN": "123456:test-token",
        "TELEGRAM_OWNER_ID": "42",
        "TELEGRAM_INBOX_CHAT_ID": "-1001",
        "TELEGRAM_OUTPUT_CHAT_ID": "-1002",
        "TELEGRAM_WEBHOOK_SECRET": "0123456789abcdef",
        "PUBLIC_BASE_URL": "https://example.test",
        "AI_API_KEY": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    }
)
