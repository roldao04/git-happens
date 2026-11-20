"""Speech to text for the phone app, on this machine's CPU: Parakeet TDT 0.6B v3 (NVIDIA), as ONNX.

It understands European languages, Portuguese and English among them, and needs no cloud. The phone
records 16 kHz mono WAV itself, so no ffmpeg is needed here (docs/phone-api.md, POST /api/voice).

Optional: `npm run setup` in mobile-interaction-interface/ asks whether to install it (`uv sync --extra voice`
and the int8 model, about 650 MB). Without it, available() is False, the API answers 501, and
the app falls back to the browser's own speech recognition.
"""
import importlib.util
import logging
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("source.voice")

MODEL = "nemo-parakeet-tdt-0.6b-v3"
QUANTIZATION = "int8"  # about 650 MB instead of 2.5 GB, and a little faster on a laptop CPU (measured: 4 s of audio in about 1 s)
MAX_BYTES = 4_000_000  # about 2 min of 16 kHz mono 16-bit audio; a spoken request is a few seconds

_model = None
_lock = threading.Lock()


class VoiceUnavailable(RuntimeError):
    pass


def available() -> bool:
    return importlib.util.find_spec("onnx_asr") is not None


def _load():
    global _model
    with _lock:
        if _model is None:
            if not available():
                raise VoiceUnavailable("voice not installed (uv sync --extra voice)")
            import onnx_asr

            started = time.monotonic()
            _model = onnx_asr.load_model(MODEL, quantization=QUANTIZATION)  # downloads once from Hugging Face, then cached
            log.info("voice model %s loaded in %.1fs", MODEL, time.monotonic() - started)
    return _model


def transcribe(wav: bytes) -> dict:
    """WAV bytes -> {"text", "lang", "ms"}. `lang` is None: the model doesn't say which language it heard,
    and the chat replies in the language of the text anyway."""
    if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
        raise ValueError("send a WAV file (16 kHz, mono)")
    if len(wav) > MAX_BYTES:
        raise ValueError("that recording is too long")
    model = _load()
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "speech.wav"
        path.write_bytes(wav)
        text = model.recognize(str(path))
    ms = int((time.monotonic() - started) * 1000)
    log.info("voice: %d ms for %d bytes", ms, len(wav))
    return {"text": str(text).strip(), "lang": None, "ms": ms}


async def _serve(port: int) -> None:
    """Only /api/voice, with no loop and no kit: the demo house's microphone (npm run demo).
    The phone API's server and checks, minus the house; its other routes answer "not read the house yet"."""
    import asyncio

    from .phone_api import PhoneApi

    api = PhoneApi(None, None, None, household="voice only", dry_run=True, stale_ms=1)
    api.serve(port)  # first, so the app finds the microphone at once; a request meanwhile waits for the model
    print(f"voice only on http://127.0.0.1:{port}/api/voice", flush=True)
    started = time.monotonic()
    await asyncio.to_thread(_load)
    print(f"model {MODEL} ready ({time.monotonic() - started:.1f}s)", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        api.close()


def main() -> None:
    """  uv run --extra voice python -m source.voice --download     # fetch the model once (npm run setup does this)
      uv run --extra voice python -m source.voice speech.wav    # what it hears, and how long it took
      uv run --extra voice python -m source.voice --serve 4790  # only /api/voice, for npm run demo"""
    import argparse

    p = argparse.ArgumentParser(prog="source.voice", description="Speech to text on this computer (Parakeet TDT 0.6B v3)")
    p.add_argument("--download", action="store_true", help="download and load the model, then stop")
    p.add_argument("--serve", type=int, metavar="PORT", help="serve only /api/voice on this port, with no loop")
    p.add_argument("wav", nargs="*", help="16 kHz mono WAV files to transcribe")
    args = p.parse_args()
    if not available():
        raise SystemExit("voice not installed: npm run setup -- --voice (in mobile-interaction-interface/)")
    if args.serve:
        import asyncio

        try:
            asyncio.run(_serve(args.serve))
        except KeyboardInterrupt:
            pass
        return
    started = time.monotonic()
    _load()
    print(f"model {MODEL} ready ({time.monotonic() - started:.1f}s)")
    for f in args.wav:
        out = transcribe(Path(f).read_bytes())
        print(f"{f}: {out['text']!r} ({out['ms']} ms)")


if __name__ == "__main__":
    main()
