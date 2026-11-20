"""Alfred's ear on the camera (source/ear.py): the loose wake word, the overlapping pieces, no double
firing, the relay failing, and the checks before it starts. ffmpeg, the model and the agent are fakes.

  uv run python -m unittest tests.test_ear -v
"""
import asyncio
import inspect
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import wave
from pathlib import Path

from source import ear

URL = "rtsp://relay.example:8554/secret-stream"


class HeardRequest(unittest.TestCase):
    def test_the_name_as_the_camera_hears_it_wakes(self):
        for text in ["Alfred, turn on the lamp.", "Alfra ligue ofra. Liga Lambda.", "ALFREDO liga a luz"]:
            self.assertEqual(ear.heard_request(text), text.strip())

    def test_talk_without_the_name_does_not_wake(self):
        for text in ["", "   ", "Eu acho que é fácil.", "Okay.", "the half of it"]:
            self.assertIsNone(ear.heard_request(text))


class AsWav(unittest.TestCase):
    def test_pcm_becomes_a_16_khz_mono_wav(self):
        pcm = b"\x01\x00" * 16000
        with wave.open(io.BytesIO(ear.as_wav(pcm)), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
            self.assertEqual(w.readframes(w.getnframes()), pcm)


class FfmpegArgs(unittest.TestCase):
    def test_only_audio_over_tcp_without_end_as_16_khz_raw_pcm_on_stdout(self):
        args = ear.ffmpeg_args(URL)
        self.assertEqual(args[args.index("-rtsp_transport") + 1], "tcp")
        self.assertEqual(args[args.index("-allowed_media_types") + 1], "audio")
        self.assertLess(args.index("-allowed_media_types"), args.index("-i"))
        self.assertEqual(args[args.index("-i") + 1], URL)
        self.assertNotIn("-t", args)
        self.assertEqual(args[args.index("-ar") + 1], "16000")
        self.assertEqual(args[args.index("-ac") + 1], "1")
        self.assertEqual(args[args.index("-f") + 1], "s16le")
        self.assertEqual(args[-1], "-")


def fake_ffmpeg(body: str) -> list[str]:
    """A stand-in for ffmpeg: a Python script that receives the same arguments."""
    script = Path(tempfile.mkdtemp()) / "ffmpeg.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


PIECE_S = 0.01  # 320 bytes of 16 kHz 16-bit audio
PIECE = b"\x02\x00" * 160


def streaming_ffmpeg(starts: Path, *, fail_first: bool = False) -> list[str]:
    """Writes audio until killed; each start appends its pid to `starts`; the first start can fail instead."""
    return fake_ffmpeg(f"""
        import os, sys, time
        starts = {str(starts)!r}
        first = not os.path.exists(starts)
        with open(starts, "a") as f:
            f.write(str(os.getpid()) + "\\n")
        if {fail_first!r} and first:
            sys.stderr.write("Error opening input file {URL}")
            sys.exit(1)
        while True:
            sys.stdout.buffer.write(b"\\x02\\x00" * 100)
            sys.stdout.buffer.flush()
            time.sleep(0.005)
    """)


def started(starts: Path) -> list[int]:
    return [int(line) for line in starts.read_text().split()] if starts.exists() else []


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class Stream(unittest.TestCase):
    def setUp(self):
        self.starts = Path(tempfile.mkdtemp()) / "starts"

    def stream(self, program, timeout_s=5.0) -> ear.CameraStream:
        return ear.CameraStream(URL, piece_s=PIECE_S, timeout_s=timeout_s, program=program)

    def test_pieces_come_from_one_session_with_the_relay(self):
        async def two_pieces(stream):
            try:
                return [await stream.read(), await stream.read()]
            finally:
                await stream.close()

        pieces = asyncio.run(two_pieces(self.stream(streaming_ffmpeg(self.starts))))
        self.assertEqual(pieces, [PIECE, PIECE])
        self.assertEqual(len(started(self.starts)), 1)

    def test_a_failure_says_why_without_the_camera_address(self):
        program = fake_ffmpeg(f"import sys; sys.stderr.write('Error opening input file {URL}'); sys.exit(1)")
        with self.assertRaises(ear.EarError) as caught:
            asyncio.run(self.stream(program).read())
        self.assertIn("Error opening input", str(caught.exception))
        self.assertNotIn(URL, str(caught.exception))
        self.assertNotIn("relay.example", str(caught.exception))

    def test_a_relay_that_goes_silent_times_out_and_ffmpeg_is_stopped(self):
        pid_file = Path(tempfile.mkdtemp()) / "pid"
        program = fake_ffmpeg(f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)")
        with self.assertRaises(ear.EarError) as caught:
            asyncio.run(self.stream(program, timeout_s=0.5).read())
        self.assertIn("no audio in 0.5s", str(caught.exception))
        self.assertTrue(gone(int(pid_file.read_text())))

    def test_by_default_the_relay_gets_45_s_to_send_audio(self):
        default = inspect.signature(ear.CameraStream).parameters["timeout_s"].default
        self.assertEqual(default, 45.0)

    def test_after_a_failure_the_next_piece_starts_a_new_session(self):
        async def fail_then_read(stream):
            try:
                with self.assertRaises(ear.EarError):
                    await stream.read()
                return await stream.read()
            finally:
                await stream.close()

        piece = asyncio.run(fail_then_read(self.stream(streaming_ffmpeg(self.starts, fail_first=True))))
        self.assertEqual(piece, PIECE)
        self.assertEqual(len(started(self.starts)), 2)

    def test_closing_stops_ffmpeg(self):
        async def read_then_close(stream):
            await stream.read()
            await stream.close()

        asyncio.run(read_then_close(self.stream(streaming_ffmpeg(self.starts))))
        self.assertTrue(gone(started(self.starts)[0]))


class FakeHouse:
    """Pieces the camera "records", what the model "hears" for each joined clip, and what Alfred was asked."""

    def __init__(self, pieces: list[bytes], heard: dict[bytes, str]):
        self.pieces = list(pieces)
        self.heard = heard
        self.clips: list[bytes] = []
        self.asked: list[str] = []

    async def record(self) -> bytes:
        piece = self.pieces.pop(0)
        if isinstance(piece, Exception):
            raise piece
        return piece

    def transcribe(self, wav: bytes) -> dict:
        with wave.open(io.BytesIO(wav), "rb") as w:
            pcm = w.readframes(w.getnframes())
        self.clips.append(pcm)
        return {"text": self.heard.get(pcm, ""), "lang": None, "ms": 1}

    async def ask(self, text: str) -> dict:
        self.asked.append(text)
        return {"text": "ok"}

    async def close(self) -> None:
        self.closed = True

    def ear(self) -> ear.Ear:
        self.closed = False
        return ear.Ear(self.record, self.transcribe, self.ask, retry_s=0, close=self.close)


A, B, C, D = b"\x0a\x00" * 4, b"\x0b\x00" * 4, b"\x0c\x00" * 4, b"\x0d\x00" * 4


class Step(unittest.TestCase):
    def test_each_step_hears_the_last_two_pieces(self):
        house = FakeHouse([A, B, C], {})
        e = house.ear()
        for _ in range(3):
            asyncio.run(e.step())
        self.assertEqual(house.clips, [A, A + B, B + C])

    def test_a_name_across_two_pieces_is_heard_and_asked(self):
        house = FakeHouse([A, B], {A + B: "Alfred, liga a lâmpada"})
        e = house.ear()
        self.assertIsNone(asyncio.run(e.step()))
        self.assertEqual(asyncio.run(e.step()), "Alfred, liga a lâmpada")
        self.assertEqual(house.asked, ["Alfred, liga a lâmpada"])

    def test_after_asking_the_same_words_are_not_asked_again(self):
        house = FakeHouse([A, B, C], {A: "Alfred, liga a luz", A + B: "Alfred, liga a luz"})
        e = house.ear()
        asyncio.run(e.step())
        asyncio.run(e.step())
        asyncio.run(e.step())
        self.assertEqual(house.asked, ["Alfred, liga a luz"])
        self.assertEqual(house.clips, [A, B, B + C])

    def test_plain_talk_is_never_asked(self):
        house = FakeHouse([A, B], {A: "Eu acho que é fácil.", A + B: "Eu acho que é fácil. Okay."})
        e = house.ear()
        asyncio.run(e.step())
        asyncio.run(e.step())
        self.assertEqual(house.asked, [])


class Run(unittest.TestCase):
    def test_the_relay_failing_is_logged_and_listening_goes_on(self):
        house = FakeHouse([ear.EarError("no audio in 20s"), A], {A: "Alfred, are you there?"})
        e = house.ear()
        with self.assertLogs("source.ear", "WARNING") as logs:
            asyncio.run(e.run(max_steps=2))
        self.assertEqual(house.asked, ["Alfred, are you there?"])
        self.assertIn("no audio in 20s", "\n".join(logs.output))

    def test_one_warning_per_outage_and_one_line_when_it_is_back(self):
        house = FakeHouse([ear.EarError("no audio in 20s"), ear.EarError("Invalid data"), ear.EarError("404"), A,
                           ear.EarError("no audio in 20s")], {})
        e = house.ear()
        with self.assertLogs("source.ear", "INFO") as logs:
            asyncio.run(e.run(max_steps=5))
        lines = [line for line in logs.output if not line.startswith("DEBUG")]
        self.assertEqual([line.split(":")[0] for line in lines], ["WARNING", "INFO", "WARNING"])
        self.assertIn("no audio in 20s", lines[0])
        self.assertIn("back", lines[1])

    def test_stopping_closes_the_stream(self):
        house = FakeHouse([A, B], {})
        e = house.ear()
        asyncio.run(e.run(max_steps=2))
        self.assertTrue(house.closed)

    def test_being_cancelled_closes_the_stream(self):
        house = FakeHouse([], {})

        async def never():
            await asyncio.sleep(30)
            return A

        house.record = never

        async def cancel_while_listening():
            task = asyncio.create_task(house.ear().run())
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_while_listening())
        self.assertTrue(house.closed)

    def test_alfred_failing_to_answer_is_logged_and_listening_goes_on(self):
        house = FakeHouse([A, B, C], {A: "Alfred, one", B + C: "Alfred, two"})
        calls = []

        async def ask(text):
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("the loop hasn't read the house yet")
            return {"text": "ok"}

        e = ear.Ear(house.record, house.transcribe, ask, retry_s=0)
        with self.assertLogs("source.ear", "WARNING") as logs:
            asyncio.run(e.run(max_steps=3))
        self.assertEqual(calls, ["Alfred, one", "Alfred, two"])
        self.assertIn("hasn't read the house", "\n".join(logs.output))


class ForApi(unittest.TestCase):
    def test_what_is_heard_goes_to_the_phone_chat_as_a_person(self):
        asked = []

        class Api:
            async def chat(self, text, lang):
                asked.append((text, lang))
                return {"text": "ok"}

        class Stream:
            async def read(self):
                return A

            async def close(self):
                pass

        e = ear.for_api(Api(), URL, transcribe=lambda wav: {"text": "Alfred, liga a luz"}, stream=Stream())
        asyncio.run(e.step())
        self.assertEqual(asked, [("Alfred, liga a luz", None)])


class Command(unittest.TestCase):
    def test_hear_camera_without_the_api_refuses_to_start(self):
        out = subprocess.run([sys.executable, "-m", "source", "--hear-camera", "--quiet"], capture_output=True, check=False,
                             text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--hear-camera needs --api", out.stderr)


class Problem(unittest.TestCase):
    def test_ready_when_the_api_the_address_and_the_model_are_there(self):
        self.assertIsNone(ear.problem(api=True, url=URL, voice_available=True))

    def test_needs_the_api(self):
        self.assertIn("--api", ear.problem(api=False, url=URL, voice_available=True))

    def test_needs_the_camera_address(self):
        for url in [None, "", "  "]:
            self.assertIn("CAMERA_RTSP_URL", ear.problem(api=True, url=url, voice_available=True))

    def test_needs_the_speech_model(self):
        self.assertIn("voice", ear.problem(api=True, url=URL, voice_available=False))


if __name__ == "__main__":
    unittest.main()
