# Source and media licensing

Audio-to-score transcription runs the bundled Spotify Basic Pitch ONNX model
(Apache-2.0) using ONNX Runtime (MIT). The model, license and checksum are in
`assets/models/basic-pitch/`. Song separation uses the optional Demucs engine
already described below; model weights may be downloaded on first use.

The Scoring workspace uses [Verovio](https://www.verovio.org/) (LGPLv3)
for MusicXML engraving. Release bundles include its notation resources,
package metadata and license notices. Upstream source is available at
https://github.com/rism-digital/verovio.

The original Anharmonic Studio application source and documentation are
distributed under the [GNU General Public License v3.0 or later](LICENSE).
This does not relicense Python dependencies, optional models, external programs,
or imported media.

Dependency versions and their upstream packages are recorded in `uv.lock`.
Preserve the upstream licenses and notices when distributing dependencies.
The optional separation engine and downloaded weights retain their own terms.

External plugin hosting uses Spotify's Pedalboard 0.9.24 (GPL-3.0), including
its native host and accompanying third-party notices. MIDI input uses
python-rtmidi and RtMidi; preserve their accompanying licenses. The Linux
packager retains these packages' metadata and license files in `notices/`.
Third-party VST instruments, effects, and their presets are not bundled.

`library/`, user projects, imported songs, exports, and downloaded sample packs
are user assets, not part of the application's source license. In particular,
the MusicRadar trap pack is linked locally for music production and must not
be redistributed as part of the application. These private packs and recordings
are excluded from this repository. The website's screenshots show a disposable
synthetic session; its commercial uses an original soundtrack.

The orchestral recordings under `assets/orchestra/` are a compact selection from
[Versilian Studios Chamber Orchestra Community Edition](https://versilian-studios.com/vsco-community/),
dedicated to the public domain under CC0 1.0. Recordings: Sam Gossner and Simon
Dalzell; sample cutting: Elan Hickler / Soundemote. The full dedication is retained
in `assets/orchestra/LICENSE-CC0.txt`. The manifest records original paths, Git blob
hashes, numeric SFZ pitch/velocity mappings, and upstream revision
`6dd651d55dde97fd4028699be9d4481f26917891` of
[VSCO-2-CE](https://github.com/sgossner/VSCO-2-CE). The bundled FLAC files preserve
the original recordings losslessly; the optional installer verifies the original
WAV bytes before conversion. No Omnisphere recordings or presets are included.

## Rubber Band 4.0.0

Autotune V2 uses Rubber Band by Particular Programs Ltd., licensed GPL-2.0-or-later.
Unmodified source and COPYING are included in `native/vendor/rubberband`; the Linux
bundle carries the same source under `notices/rubberband-4.0.0-source`.
Source: https://breakfastquay.com/files/releases/rubberband-4.0.0.tar.bz2
Our CMake build uses the built-in FFT and BQ resampler, with no optional codec dependencies.

## Anharmonic Prism instrument plugin

The optional first-party plugin build uses JUCE 7.0.12 under its GPLv3 option,
matching the source engine's GPLv3-or-later license. `scripts/build_prism.py`
verifies the upstream source archive against SHA256
`94e3b35e1990cd67f59736bb5e1eff1cfd9590b16fea82696f309a8f59452434`.
The plugin pack and bundled-plugin directory include the exact JUCE source
archive and corresponding project source. Upstream module and embedded SDK
notices are preserved in that archive. VST is a trademark of Steinberg Media
Technologies GmbH. See `plugins/prism/README.md` for supported formats and build
instructions. JUCE source: https://github.com/juce-framework/JUCE/tree/7.0.12.
