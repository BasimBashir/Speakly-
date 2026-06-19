"""Self-hosted Piper HTTP TTS service (no local `piper` package required).

pipecat's ``PiperHttpTTSService`` imports the ``piper`` package at module load
even though the HTTP client never uses it. To keep the backend venv free of the
piper/onnxruntime synthesis stack — we only call the remote ``piper-tts``
container — we implement the small HTTP client directly against the Piper HTTP
API (``POST /`` with ``{"text"}`` -> WAV bytes). Behaviour mirrors pipecat's
PiperHttpTTSService. Owns its aiohttp session and closes it on cleanup.
"""

from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TTSStoppedFrame
from pipecat.services.tts_service import TTSService


class PiperOwnedSessionTTSService(TTSService):
    """Minimal HTTP client for a self-hosted Piper TTS server."""

    def __init__(
        self,
        *,
        base_url: str,
        aiohttp_session: aiohttp.ClientSession,
        voice: str | None = None,
        **kwargs,
    ):
        super().__init__(push_start_frame=True, push_stop_frames=True, **kwargs)
        self._base_url = base_url.rstrip("/")
        self._session = aiohttp_session
        self._owned_session = aiohttp_session
        self._voice = voice

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating TTS [{text}]")
        payload: dict = {"text": text}
        if self._voice:
            payload["voice"] = self._voice
        try:
            async with self._session.post(
                self._base_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status != 200:
                    error = await response.text()
                    yield ErrorFrame(
                        error=f"Piper TTS error (status {response.status}): {error}"
                    )
                    yield TTSStoppedFrame(context_id=context_id)
                    return

                await self.start_tts_usage_metrics(text)

                async for frame in self._stream_audio_frames_from_iterator(
                    response.content.iter_chunked(self.chunk_size),
                    strip_wav_header=True,
                    context_id=context_id,
                ):
                    await self.stop_ttfb_metrics()
                    yield frame
        except Exception as e:  # noqa: BLE001 — surface as a non-fatal error frame
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
        finally:
            await self.stop_ttfb_metrics()

    async def cleanup(self):
        await super().cleanup()
        if not self._owned_session.closed:
            await self._owned_session.close()
