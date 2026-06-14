from io import BytesIO

from PIL import Image

from app.services.watermark import (
    RIGHT_MARGIN_RATIO,
    TOP_MARGIN_RATIO,
    WATERMARK_WIDTH_RATIO,
    apply_watermark,
)


def make_source(width: int, height: int, image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format=image_format)
    return output.getvalue()


def test_watermark_uses_relative_size_and_offsets() -> None:
    result = apply_watermark(make_source(1920, 1080), "example.png")
    image = Image.open(BytesIO(result.content)).convert("RGB")
    pixels = image.load()

    changed = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if pixels[x, y] != (255, 255, 255)
    ]
    left = min(x for x, _ in changed)
    top = min(y for _, y in changed)
    right = max(x for x, _ in changed) + 1

    expected_width = round(image.width * WATERMARK_WIDTH_RATIO)
    expected_right_margin = round(image.width * RIGHT_MARGIN_RATIO)
    expected_top = round(image.height * TOP_MARGIN_RATIO)
    assert abs((right - left) - expected_width) <= 1
    assert abs((image.width - right) - expected_right_margin) <= 1
    assert abs(top - expected_top) <= 1


def test_watermark_preserves_jpeg_output() -> None:
    result = apply_watermark(make_source(800, 600, "JPEG"), "photo.jpeg")

    assert result.filename == "photo_watermark.jpg"
    assert Image.open(BytesIO(result.content)).format == "JPEG"
