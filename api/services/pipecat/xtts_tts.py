"""Self-hosted XTTS-Urdu HTTP TTS service.

Talks to the xtts-urdu container (POST /tts {"text"} -> WAV bytes), a voice-cloned
male Urdu voice that is cleaner than Piper. Implemented directly against the HTTP
API (only aiohttp) so the backend venv stays free of the heavy coqui-tts stack —
that lives in the xtts-urdu container. Owns its aiohttp session.

The server returns the full utterance as one WAV; we stream audio frames from it.
TTFB is higher than Piper (whole-utterance synth), so Piper remains the
low-latency fallback Voice option.
"""

from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TTSStoppedFrame
from pipecat.services.tts_service import TTSService


class XTTSUrduTTSService(TTSService):
    """Minimal HTTP client for the self-hosted xtts-urdu server."""

    def __init__(
        self,
        *,
        base_url: str,
        aiohttp_session: aiohttp.ClientSession,
        **kwargs,
    ):
        super().__init__(push_start_frame=True, push_stop_frames=True, **kwargs)
        self._base_url = base_url.rstrip("/")
        self._session = aiohttp_session
        self._owned_session = aiohttp_session

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating TTS [{text}]")
        try:
            async with self._session.post(
                f"{self._base_url}/tts",
                json={"text": text},
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status != 200:
                    error = await response.text()
                    yield ErrorFrame(
                        error=f"XTTS-Urdu error (status {response.status}): {error}"
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
