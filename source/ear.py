"""Alfred's ear on the camera: what the room says, through the camera's microphone, to the agent.

  python -m source --api --hear-camera [--look-only]

One ffmpeg session pulls only the camera's audio (CAMERA_RTSP_URL) and is read in pieces of PIECE_S
seconds; it is restarted only when it dies or goes silent, so nothing is missed between pieces. Each
step transcribes the last two pieces (source/voice.py), so a name split across two pieces is still heard. When the words
carry the wake word, they go to the agent as a person's request (PhoneApi.chat), and the pieces are
dropped so the same words are not asked twice. Talk without the wake word is never logged or kept.

The wake word is loose on purpose: at 8 kHz across a room "Alfred" is often heard as "Alfra". A
dedicated wake-word model can replace heard_request later.
"""
import asyncio
import io
import logging
import re
import wave
from urllib.parse import urlsplit

log = logging.getLogger("source.ear")

RATE = 16000
PIECE_S = 4.0
TIMEOUT_S = 20.0
RETRY_S = 5.0
WAKE = re.compile(r"\balf\w*", re.IGNORECASE)


class EarError(RuntimeError):
    pass


def heard_request(text: str) -> str | None:
    text = text.strip()
    return text if WAKE.search(text) else None


def as_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def ffmpeg_args(url: str) -> list[str]:
    return ["-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp", "-allowed_media_types", "audio", "-i", url,
            "-map", "0:a", "-ar", str(RATE), "-ac", "1", "-f", "s16le", "-"]


def _redact(text: str, url: str) -> str:
    text = re.sub(r"rtsp://\S+", "<camera>", text.replace(url, "<camera>"))
    host = urlsplit(url).hostname
    return text.replace(host, "<camera>") if host else text


class CameraStream:
    """The camera's audio as 16 kHz mono 16-bit PCM, read `piece_s` at a time from one ffmpeg session.
    Raises EarError, never with the address; the next read starts a new session."""

    def __init__(self, url: str, *, piece_s: float = PIECE_S, timeout_s: float = TIMEOUT_S,
                 program: list[str] | None = None):
        self._url = url
        self._piece_bytes = int(piece_s * RATE) * 2
        self._timeout_s = timeout_s
        self._program = program or ["ffmpeg"]
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr: asyncio.Task | None = None
        self._err = bytearray()

    async def _start(self) -> asyncio.subprocess.Process:
        self._err = bytearray()
        proc = await asyncio.create_subprocess_exec(*self._program, *ffmpeg_args(self._url),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._stderr = asyncio.create_task(self._keep_tail(proc.stderr, self._err))
        self._proc = proc
        return proc

    @staticmethod
    async def _keep_tail(stream: asyncio.StreamReader, into: bytearray) -> None:
        while chunk := await stream.read(4096):
            into.extend(chunk)
            del into[:-2000]

    async def read(self) -> bytes:
        proc = self._proc or await self._start()
        try:
            return await asyncio.wait_for(proc.stdout.readexactly(self._piece_bytes), self._timeout_s)
        except TimeoutError:
            await self.close()
            raise EarError(f"no audio in {self._timeout_s:g}s") from None
        except asyncio.IncompleteReadError:
            code = await proc.wait()
            await self.close()
            why = _redact(self._err.decode(errors="replace").strip()[-300:], self._url)
            raise EarError(f"ffmpeg stopped ({code}): {why}" if why else f"ffmpeg stopped ({code})") from None

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        task, self._stderr = self._stderr, None
        if task is not None:
            await task


class Ear:
    def __init__(self, record, transcribe, ask, *, retry_s: float = RETRY_S, close=None):
        """`record()` -> PCM (async), `transcribe(wav)` -> {"text", ...} (blocking), `ask(text)` (async),
        `close()` (async) when it stops listening."""
        self._record = record
        self._transcribe = transcribe
        self._ask = ask
        self._retry_s = retry_s
        self._close = close
        self._previous = b""

    async def step(self) -> str | None:
        piece = await self._record()
        heard = await asyncio.to_thread(self._transcribe, as_wav(self._previous + piece))
        request = heard_request(str(heard.get("text") or ""))
        if request is None:
            self._previous = piece
            return None
        self._previous = b""
        log.info("heard a request: %s", request, extra={"data": {"request": request}})
        await self._ask(request)
        return request

    async def run(self, max_steps: int | None = None) -> None:
        steps = 0
        down = False
        try:
            while max_steps is None or steps < max_steps:
                steps += 1
                try:
                    await self.step()
                except EarError as exc:
                    if down:
                        log.debug("camera audio still down: %s", exc)
                    else:
                        log.warning("camera audio stopped, retrying quietly: %s", exc)
                        down = True
                    await asyncio.sleep(self._retry_s)
                    continue
                except Exception as exc:
                    log.warning("camera ear: the request failed: %s", exc, exc_info=True)
                if down:
                    log.info("camera audio is back")
                    down = False
        finally:
            if self._close is not None:
                await self._close()


def problem(*, api: bool, url: str | None, voice_available: bool) -> str | None:
    """Why --hear-camera can't start, or None."""
    if not api:
        return "--hear-camera needs --api: what it hears goes to Alfred as the phone's chat does"
    if not url or not url.strip():
        return "--hear-camera needs CAMERA_RTSP_URL in .env"
    if not voice_available:
        return "--hear-camera needs the voice model: uv sync --extra voice"
    return None


def for_api(api, url: str, *, transcribe, stream: CameraStream | None = None) -> Ear:
    """An Ear that asks Alfred through the phone API's chat, as a person (language left to the text)."""
    async def ask(text: str) -> dict:
        return await api.chat(text, None)

    stream = stream or CameraStream(url)
    return Ear(stream.read, transcribe, ask, close=stream.close)
