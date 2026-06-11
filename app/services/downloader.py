import asyncio
import logging
import tempfile
from pathlib import Path

import yt_dlp  # type: ignore[import-untyped]
from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import Settings
from app.models import ContentItem

logger = logging.getLogger(__name__)
MAX_DOWNLOAD_BYTES = 49 * 1024 * 1024


class DownloadError(RuntimeError):
    pass


class MediaDownloader:
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        self.lock = asyncio.Lock()

    async def send(self, item: ContentItem) -> None:
        if item.kind == "telegram":
            if not item.source_chat_id or not item.source_message_id:
                raise DownloadError("У исходного сообщения нет доступного видео.")
            await self.bot.copy_message(
                self.settings.telegram_owner_id,
                item.source_chat_id,
                item.source_message_id,
            )
            return
        if item.kind != "youtube":
            raise DownloadError("Этот материал нельзя скачать.")
        async with self.lock:
            await self._send_youtube(item)

    async def _send_youtube(self, item: ContentItem) -> None:
        with tempfile.TemporaryDirectory(prefix="parser-download-") as directory:
            path = await asyncio.wait_for(
                asyncio.to_thread(self._download_youtube, item.url, Path(directory)),
                timeout=900,
            )
            if path.stat().st_size > MAX_DOWNLOAD_BYTES:
                raise DownloadError(
                    "Видео больше 49 МБ даже в минимальном доступном качестве."
                )
            try:
                await self.bot.send_video(
                    self.settings.telegram_owner_id,
                    FSInputFile(path),
                    caption=item.title[:1024],
                    supports_streaming=True,
                    request_timeout=300,
                )
            except Exception:
                logger.exception("send_video failed, retrying as document")
                await self.bot.send_document(
                    self.settings.telegram_owner_id,
                    FSInputFile(path),
                    caption=item.title[:1024],
                    request_timeout=300,
                )

    @staticmethod
    def _download_youtube(url: str, directory: Path) -> Path:
        output = str(directory / "%(id)s.%(ext)s")
        options = {
            "format": (
                "best[ext=mp4][filesize<=49M]/"
                "best[ext=mp4][filesize_approx<=49M]/"
                "best[filesize<=49M]/best[filesize_approx<=49M]"
            ),
            "outtmpl": output,
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "extractor_args": {
                "youtube": {"player_client": ["mweb"]},
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://pot-provider:4416"]
                },
            },
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
                filename = Path(downloader.prepare_filename(info))
        except Exception as error:
            raise DownloadError(f"Не удалось скачать видео: {error}") from error
        if filename.exists():
            return filename
        files = [path for path in directory.iterdir() if path.is_file()]
        if not files:
            raise DownloadError("YouTube не вернул файл подходящего размера.")
        return max(files, key=lambda path: path.stat().st_size)
