"""PII-redacting wrapper around browser-use's BaseChatModel.

Implements ADR-0020:
  - Capa 1: text/DOM scrubbing via RedactFilter.scrub() on every message part.
  - Capa 2: image blackout via Pillow on ContentPartImageParam (data-URL PNG/JPEG).
    Only active when pii_regions is non-empty (onboarding gap documented in ADR-0020).

Architecture note:
    PIIRedactingChatModel wraps any BaseChatModel (Protocol), intercepts ainvoke,
    scrubs messages, then delegates to the inner model.  This is the cleanest
    interception point without patching browser-use internals.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from browser_use.llm.messages import (
    BaseMessage,
    ContentPartImageParam,
    ContentPartTextParam,
    SystemMessage,
    UserMessage,
)
from browser_use.llm.views import ChatInvokeCompletion
from pydantic import BaseModel

from open_banca_observability.redact import RedactConfig, RedactFilter

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Regex to detect data-URL images: data:<mime>;base64,<data>
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)


@dataclass
class PiiRegion:
    """A rectangular region (pixel coords) to black-out in screenshots.

    Coordinates are relative to the full-page screenshot at the time of capture.
    All values are integers in CSS pixels (device-independent pixels).
    """

    x: int
    y: int
    width: int
    height: int


class PIIRedactingChatModel:
    """Wraps a BaseChatModel to scrub PII from messages before they reach the LLM.

    Capa 1 (text): applies RedactFilter.scrub() to all text parts and system messages.
    Capa 2 (image): blacks out ``pii_regions`` rectangles in base64-encoded screenshots.

    Args:
        inner: The underlying chat model (ChatLiteLLM or any BaseChatModel).
        redact_config: Optional RedactConfig; defaults to env-var canaries.
        pii_regions: Pixel-coordinate regions to black out in screenshots.
                     Empty during the first onboarding run (ADR-0020 gap).
    """

    # Required by BaseChatModel Protocol
    _verified_api_keys: bool = False

    def __init__(
        self,
        inner: Any,
        redact_config: RedactConfig | None = None,
        pii_regions: list[PiiRegion] | None = None,
    ) -> None:
        self._inner: Any = inner
        self._filter = RedactFilter(config=redact_config)
        self._pii_regions: list[PiiRegion] = pii_regions or []

    # ── Protocol attributes forwarded to inner ──────────────────────────────

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def model_name(self) -> str:
        """Proxy attribute required by browser-use telemetry."""
        return str(getattr(self._inner, "model_name", self._inner.model) or "")

    @property
    def provider(self) -> str:
        return self._inner.provider  # type: ignore[attr-defined]

    @property
    def name(self) -> str:
        return self._inner.name  # type: ignore[attr-defined]

    # ── PII scrubbing helpers ────────────────────────────────────────────────

    def _scrub_text(self, text: str) -> str:
        return self._filter.scrub(text)

    def _scrub_image_data_url(self, url: str) -> str:
        """Apply black-out rectangles to a base64 image data-URL.

        Returns the original URL unchanged if:
          - No pii_regions configured (onboarding run).
          - URL is not a data: scheme (remote URL — not expected in browser-use).
          - Pillow is not available.
        """
        if not self._pii_regions:
            return url  # ADR-0020 onboarding gap: no regions yet

        m = _DATA_URL_RE.match(url)
        if not m:
            return url  # not a base64 data URL; leave as-is

        mime = m.group("mime")
        raw = base64.b64decode(m.group("data"))

        try:
            from PIL import Image, ImageDraw  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("Pillow not available — skipping Capa 2 image PII redaction")
            return url

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        draw = ImageDraw.Draw(img)
        for region in self._pii_regions:
            draw.rectangle(
                [region.x, region.y, region.x + region.width, region.y + region.height],
                fill=(0, 0, 0, 255),
            )

        buf = io.BytesIO()
        fmt = "PNG" if "png" in mime.lower() else "JPEG"
        img.save(buf, format=fmt)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _scrub_content_part(
        self, part: ContentPartTextParam | ContentPartImageParam
    ) -> ContentPartTextParam | ContentPartImageParam:
        if part.type == "text":
            scrubbed = self._scrub_text(part.text)
            if scrubbed == part.text:
                return part
            return ContentPartTextParam(type="text", text=scrubbed)
        elif part.type == "image_url":
            scrubbed_url = self._scrub_image_data_url(part.image_url.url)
            if scrubbed_url == part.image_url.url:
                return part
            from browser_use.llm.messages import ImageURL  # type: ignore[attr-defined]

            new_image_url = ImageURL(
                url=scrubbed_url,
                detail=part.image_url.detail,
                media_type=part.image_url.media_type,
            )
            return ContentPartImageParam(image_url=new_image_url)
        return part

    def _scrub_message(self, msg: BaseMessage) -> BaseMessage:
        if isinstance(msg, SystemMessage):
            content = msg.content
            if isinstance(content, str):
                scrubbed = self._scrub_text(content)
                if scrubbed != content:
                    return SystemMessage(role="system", content=scrubbed)
            elif isinstance(content, list):
                new_parts = [self._scrub_content_part(p) for p in content]
                return SystemMessage(role="system", content=new_parts)  # type: ignore[arg-type]
            return msg

        if isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, str):
                scrubbed = self._scrub_text(content)
                if scrubbed != content:
                    return UserMessage(role="user", content=scrubbed)
            elif isinstance(content, list):
                new_parts = [self._scrub_content_part(p) for p in content]
                return UserMessage(role="user", content=new_parts)  # type: ignore[arg-type]
            return msg

        # AssistantMessage: no scrubbing needed (outbound from model)
        return msg

    def scrub_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Return a new list with PII scrubbed from all messages."""
        return [self._scrub_message(m) for m in messages]

    # ── BaseChatModel Protocol ────────────────────────────────────────────────

    @overload
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: type[T],
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        scrubbed = self.scrub_messages(messages)
        if output_format is not None:
            return await self._inner.ainvoke(scrubbed, output_format, **kwargs)
        return await self._inner.ainvoke(scrubbed, **kwargs)
