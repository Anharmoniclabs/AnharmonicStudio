"""Shared engine limits and cue identities; no platform or device setup."""

FADE = 0.004  # seconds, used for chokes and panics
MAX_SYNTH_VOICES = 8
MAX_PAD_VOICES = 64
AUDITION = -2  # pad index reserved for the CHOP editor's preview voice
METRONOME = -3  # cue bus; independent of project Track 1
CALLBACK_HISTORY = 512  # ~11 s of callbacks at 1024 frames, ~1.4 s at 128
SEND_TAIL = 6.0  # seconds the sends keep running after the last send
TRACK_DSP_TAIL = 1.0  # let FIR/compressor state drain, then put it to sleep
OFFLINE_VOICE_CHUNK = 16_384  # bound bounce scratch to about 1.1 MiB
