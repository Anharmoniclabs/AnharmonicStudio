# Source and media licensing

The original Anharmonic Studio application source and documentation are
distributed under the [GNU General Public License v2.0 or later](LICENSE).
This does not relicense Python dependencies, optional models, external programs,
or imported media.

Dependency versions and their upstream packages are recorded in `uv.lock`.
Preserve the upstream licenses and notices when distributing dependencies.
The optional separation engine and downloaded weights retain their own terms.

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
