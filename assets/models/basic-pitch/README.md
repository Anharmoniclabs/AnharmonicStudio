# Basic Pitch note transcription model

Copyright Spotify AB, Apache License 2.0. See LICENSE in this directory.

This is Spotify's ICASSP 2022 Basic Pitch model exported to ONNX, downloaded
from the upstream project on 2026-09-15:
https://github.com/spotify/basic-pitch/blob/main/basic_pitch/saved_models/icassp_2022/nmp.onnx

SHA-256: `2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec`

The application runs it locally through ONNX Runtime. Input is mono 22,050 Hz
audio in 43,844-sample windows. Pitched note/onset activations cover MIDI 21–108.
The application's decoder preserves polyphony and estimates note boundaries;
it does not classify instruments or transcribe expressive pitch bends.
Demucs supplies the instrument groups in the song modes.
