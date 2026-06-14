from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

WATERMARK_PATH = Path(__file__).resolve().parent.parent / "assets" / "watermark.png"
WATERMARK_WIDTH_RATIO = 0.12
RIGHT_MARGIN_RATIO = 0.043
TOP_MARGIN_RATIO = 0.075
MAX_IMAGE_PIXELS = 40_000_000


class WatermarkError(ValueError):
    pass


@dataclass(slots=True)
class WatermarkedImage:
    content: bytes
    filename: str


def apply_watermark(
    image_content: bytes,
    source_filename: str = "image.jpg",
    watermark_path: Path = WATERMARK_PATH,
) -> WatermarkedImage:
    try:
        with Image.open(BytesIO(image_content)) as opened:
            source = ImageOps.exif_transpose(opened)
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise WatermarkError("Изображение слишком большое.")
            canvas = source.convert("RGBA")
    except WatermarkError:
        raise
    except Exception as error:
        raise WatermarkError("Не удалось прочитать изображение.") from error

    with Image.open(watermark_path) as opened_mark:
        mark = opened_mark.convert("RGBA")

    target_width = max(1, round(canvas.width * WATERMARK_WIDTH_RATIO))
    target_height = max(1, round(mark.height * target_width / mark.width))
    mark = mark.resize((target_width, target_height), Image.Resampling.LANCZOS)

    right_margin = round(canvas.width * RIGHT_MARGIN_RATIO)
    top_margin = round(canvas.height * TOP_MARGIN_RATIO)
    x = max(0, canvas.width - right_margin - mark.width)
    y = min(max(0, top_margin), max(0, canvas.height - mark.height))
    canvas.alpha_composite(mark, (x, y))

    suffix = Path(source_filename).suffix.casefold()
    output = BytesIO()
    if suffix in {".jpg", ".jpeg"}:
        canvas.convert("RGB").save(output, format="JPEG", quality=95, optimize=True)
        filename = f"{Path(source_filename).stem}_watermark.jpg"
    else:
        canvas.save(output, format="PNG", optimize=True)
        filename = f"{Path(source_filename).stem}_watermark.png"
    return WatermarkedImage(output.getvalue(), filename)
