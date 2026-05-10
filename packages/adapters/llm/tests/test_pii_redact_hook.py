"""Test: PIIRedactingChatModel scrubs PII canary from messages before they reach LLM.

ADR-0020 Capa 1: text/DOM scrubbing.
ADR-0020 Capa 2: image blackout (tested with a synthetic PNG).
"""

from __future__ import annotations

import base64
import io

import pytest
from browser_use.llm.messages import ContentPartImageParam, ContentPartTextParam, UserMessage

from open_banca_llm.mapper.agent import FakeChatModel
from open_banca_llm.mapper.pii_filter_adapter import PIIRedactingChatModel, PiiRegion
from open_banca_observability.redact import RedactConfig

PII_CANARY_NAME = "Juan_Perez_Canary_9827364"


@pytest.fixture()
def redact_config() -> RedactConfig:
    return RedactConfig(pii_name=PII_CANARY_NAME)


@pytest.fixture()
def fake_llm() -> FakeChatModel:
    return FakeChatModel(responses=['{"result": "ok"}'])


@pytest.mark.asyncio()
async def test_pii_name_canary_scrubbed_from_text_message(
    fake_llm: FakeChatModel,
    redact_config: RedactConfig,
) -> None:
    """PII_CANARY_NAME in a text UserMessage is replaced by [REDACTED] before LLM."""
    wrapped = PIIRedactingChatModel(inner=fake_llm, redact_config=redact_config)
    messages = [UserMessage(role="user", content=f"Account holder: {PII_CANARY_NAME}")]
    await wrapped.ainvoke(messages)

    # The fake LLM recorded what it actually received
    received = fake_llm.recorded_messages[0][0]
    assert isinstance(received, UserMessage)
    assert PII_CANARY_NAME not in str(received.content)
    assert "[REDACTED]" in str(received.content)


@pytest.mark.asyncio()
async def test_pii_scrubbed_from_multipart_text_part(
    fake_llm: FakeChatModel,
    redact_config: RedactConfig,
) -> None:
    """PII in ContentPartTextParam within a multipart message is scrubbed."""
    wrapped = PIIRedactingChatModel(inner=fake_llm, redact_config=redact_config)
    content = [
        ContentPartTextParam(type="text", text=f"Name: {PII_CANARY_NAME}"),
        ContentPartTextParam(type="text", text="Normal text"),
    ]
    messages = [UserMessage(role="user", content=content)]
    await wrapped.ainvoke(messages)

    received = fake_llm.recorded_messages[0][0]
    assert isinstance(received, UserMessage)
    full_text = " ".join(
        p.text
        for p in received.content  # type: ignore[union-attr]
        if hasattr(p, "text")
    )
    assert PII_CANARY_NAME not in full_text


@pytest.mark.asyncio()
async def test_image_pii_region_blacked_out(fake_llm: FakeChatModel) -> None:
    """Capa 2: pixels in pii_regions are blacked out in base64 PNG before LLM."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("Pillow not installed — Capa 2 test skipped")

    # Create a 100x100 white PNG with a red pixel at (10, 10)
    img = _make_test_image_with_marker(width=100, height=100, marker_xy=(10, 10))
    data_url = _to_data_url(img)

    region = PiiRegion(x=5, y=5, width=20, height=20)
    wrapped = PIIRedactingChatModel(
        inner=fake_llm,
        redact_config=RedactConfig(),
        pii_regions=[region],
    )

    from browser_use.llm.messages import ImageURL

    image_part = ContentPartImageParam(
        image_url=ImageURL(url=data_url, detail="high", media_type="image/png")
    )
    messages = [UserMessage(role="user", content=[image_part])]
    await wrapped.ainvoke(messages)

    received = fake_llm.recorded_messages[0][0]
    assert isinstance(received, UserMessage)
    received_parts = received.content  # type: ignore[union-attr]
    assert isinstance(received_parts, list)
    image_received = received_parts[0]
    assert image_received.type == "image_url"

    # Decode and verify the pixel at (10,10) is now black
    received_url = image_received.image_url.url
    received_img = _decode_data_url(received_url)
    pixel = received_img.getpixel((10, 10))  # type: ignore[attr-defined]
    # Black pixel = (0, 0, 0) or (0, 0, 0, 255) in RGBA
    assert pixel[0] == 0 and pixel[1] == 0 and pixel[2] == 0, (
        f"Expected black pixel at (10,10), got {pixel}"
    )


@pytest.mark.asyncio()
async def test_no_pii_regions_image_unchanged(fake_llm: FakeChatModel) -> None:
    """Capa 2 onboarding gap: without pii_regions, image data URL is NOT modified."""
    img = _make_test_image_with_marker(width=50, height=50, marker_xy=(10, 10))
    original_url = _to_data_url(img)

    wrapped = PIIRedactingChatModel(
        inner=fake_llm,
        redact_config=RedactConfig(),
        pii_regions=[],  # no regions — onboarding gap
    )

    from browser_use.llm.messages import ImageURL

    image_part = ContentPartImageParam(
        image_url=ImageURL(url=original_url, detail="high", media_type="image/png")
    )
    messages = [UserMessage(role="user", content=[image_part])]
    await wrapped.ainvoke(messages)

    received = fake_llm.recorded_messages[0][0]
    received_parts = received.content  # type: ignore[union-attr]
    assert received_parts[0].image_url.url == original_url  # type: ignore[union-attr]


# ── Image helpers ─────────────────────────────────────────────────────────────


def _make_test_image_with_marker(width: int, height: int, marker_xy: tuple[int, int]) -> object:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    x, y = marker_xy
    draw.rectangle([x, y, x + 2, y + 2], fill=(255, 0, 0, 255))
    return img


def _to_data_url(img: object) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")  # type: ignore[attr-defined]
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _decode_data_url(url: str) -> object:
    from PIL import Image

    prefix = "data:image/png;base64,"
    assert url.startswith(prefix)
    raw = base64.b64decode(url[len(prefix) :])
    return Image.open(io.BytesIO(raw)).convert("RGBA")
