"""Persian speech-to-text via the configurable 'stt' role (audio-input chat
model on OpenRouter, e.g. Gemini Flash). Non-wav/mp3 audio is converted with
ffmpeg when available."""

import asyncio
import base64
import logging
import shutil
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.llm import LLMFailure, run_role
from app.services import chatwoot_client

log = logging.getLogger(__name__)

STT_INSTRUCTIONS = (
    "Transcribe this Persian (Farsi) voice message exactly, in Persian script. "
    "Output ONLY the transcription text. If the audio is silent or unintelligible, "
    "output exactly: [unintelligible]"
)

_NATIVE_FORMATS = {"wav", "mp3"}


async def _convert_to_mp3(data: bytes, ext: str) -> bytes | None:
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg not available; cannot convert .%s audio", ext)
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in.{ext}"
        dst = Path(tmp) / "out.mp3"
        src.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(src), "-ac", "1", "-b:a", "48k", str(dst),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and dst.exists():
            return dst.read_bytes()
    return None


def _extension(url: str, content_type: str = "") -> str:
    path = url.split("?")[0]
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if not ext and "/" in content_type:
        ext = content_type.split("/")[-1]
    return ext or "ogg"


async def transcribe_url(data_url: str) -> tuple[str | None, dict]:
    """Download a Chatwoot audio attachment and transcribe it.

    Returns (text, telemetry). text is None when transcription failed or was
    unintelligible."""
    telemetry: dict = {"model_used": "", "cost_usd": 0.0, "latency_ms": 0, "error": ""}
    try:
        audio = await chatwoot_client.download_attachment(data_url)
    except Exception as e:  # noqa: BLE001
        telemetry["error"] = f"download failed: {e.__class__.__name__}"
        return None, telemetry

    ext = _extension(data_url)
    fmt = ext if ext in _NATIVE_FORMATS else "mp3"
    if ext not in _NATIVE_FORMATS:
        converted = await _convert_to_mp3(audio, ext)
        if converted is None:
            telemetry["error"] = f"unsupported audio format .{ext} (no ffmpeg)"
            return None, telemetry
        audio = converted

    message = HumanMessage(content=[
        {"type": "text", "text": "Voice message to transcribe:"},
        {"type": "input_audio",
         "input_audio": {"data": base64.b64encode(audio).decode(), "format": fmt}},
    ])
    try:
        result = await run_role("stt", [SystemMessage(content=STT_INSTRUCTIONS), message])
    except LLMFailure as e:
        telemetry["error"] = str(e)[:300]
        return None, telemetry
    telemetry.update({"model_used": result.model_used, "cost_usd": result.cost_usd,
                      "latency_ms": result.latency_ms,
                      "input_tokens": result.input_tokens,
                      "output_tokens": result.output_tokens})
    text = result.output.content if isinstance(result.output.content, str) else ""
    text = text.strip()
    if not text or "[unintelligible]" in text:
        return None, telemetry
    return text, telemetry
