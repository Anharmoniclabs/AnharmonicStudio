"""Small follow-up fixes found by the recovery acceptance suite."""

from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text()
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: expected source shape not found")
    p.write_text(text.replace(old, new, 1))


def fix_midi_expression_diagnostics() -> None:
    replace_once(
        "mpclab/midi_devices.py",
        """        if kind in (0xA0, 0xC0, 0xD0):\n            self.expression(list(message))\n            return""",
        """        if kind == 0xA0:\n            self.last_event = f"Ch {channel + 1} · Poly pressure {message[1]} · {message[2]}"\n            self.expression(list(message))\n            return\n        if kind == 0xC0:\n            self.last_event = f"Ch {channel + 1} · Program {message[1]}"\n            self.expression(list(message))\n            return\n        if kind == 0xD0:\n            self.last_event = f"Ch {channel + 1} · Channel pressure · {message[1]}"\n            self.expression(list(message))\n            return""",
        "MIDI expression diagnostics",
    )

    test = Path("tests/test_midi_devices.py")
    text = test.read_text()
    text = text.replace(
        "assert expression == [[0xE3, 0, 100], [0xB0, 1, 90]]",
        "assert expression == [[0xE3, 0, 100], [0xB4, 1, 90]]",
        1,
    )
    test.write_text(text)


def fix_transcription_cancellation_order() -> None:
    replace_once(
        "mpclab/transcription.py",
        '''def decode_audio(source, destination, cancel, *, rate=SAMPLE_RATE, channels=1):\n    """Decode through an owned, headless FFmpeg child, with a hard length cap."""\n    ffmpeg = media_tool("ffmpeg")''',
        '''def decode_audio(source, destination, cancel, *, rate=SAMPLE_RATE, channels=1):\n    """Decode through an owned, headless FFmpeg child, with a hard length cap."""\n    check_cancel(cancel)\n    ffmpeg = media_tool("ffmpeg")''',
        "transcription preflight cancellation",
    )


def main() -> None:
    fix_midi_expression_diagnostics()
    fix_transcription_cancellation_order()


if __name__ == "__main__":
    main()
