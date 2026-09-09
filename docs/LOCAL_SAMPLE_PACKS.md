# Local sample packs and direct file drops

## Update and restart

Save your project and close AnharmonicStudio normally. In your existing Git
checkout, run `git switch main && git pull --ff-only origin main && ./run.sh`.
Do not overwrite a working checkout with a newly downloaded repository ZIP.

## Extract a downloaded sound pack

Keep downloaded packs outside the application source tree, for example
`~/Music/Sample Packs/Trap`. Replace `EXACT-PACK-NAME.zip` below with your actual
filename. These commands do not install or execute anything from the archive.
Use packs from sources you trust and keep their license/readme files.

```sh
mkdir -p "$HOME/Music/Sample Packs/Trap"
unzip -n "$HOME/Downloads/EXACT-PACK-NAME.zip" -d "$HOME/Music/Sample Packs/Trap"
```

`-n` skips existing files; `-d` chooses the extraction directory. Use a different
subfolder for each pack to avoid same-name collisions. An alternative when unzip
is unavailable is `python3 -m zipfile -e ARCHIVE.zip NEW-EMPTY-FOLDER`; use a fresh
folder because that alternative does not provide the same no-overwrite flag.

ZIP is the container, not the sound: extract it first, then use its WAV/FLAC/AIFF
or other supported audio files. Do not run executables included with a pack.
Do not put raw downloaded files straight in `mpclab/` or the managed `library/`.

## Browse a whole pack

Press **+ PACK** beside **+ FILE**, then select the extracted pack folder. This
uses the existing read-only pack registry: it indexes compatible audio including
nested folders without copying or changing the recordings. The Browser switches
to SAMPLE PACKS and clears stale search/crate/folder filters. Pack names and folder
paths participate in search. Keep the registered folder at the same location;
linked projects depend on it. This is library configuration, not a project edit.

For normal managed copies instead, use **+ FILE** or drag individual files onto
the Sounds browser. The managed importer creates its own metadata and WAV copy.

## Drop directly into Beats or Notes

- Drag one audio file from your Linux file manager onto a Beats lane's **name/header**
  to assign or replace that sound, retaining its musical events and mixer route.
- Drop one or several audio files on the **Add sound footer** or directly on the
  **Beats tab** to fill unused slots. Existing sounds and musically reserved empty
  slots are not overwritten. The whole selection must fit the current 16-pad bank;
  otherwise nothing is imported. Choose another bank or fewer files.
- Drop on **Notes** or its note grid to create sample instruments. A single file
  dropped on the Notes sound selector explicitly replaces that instrument.
- Hovering over Beats/Notes reveals the editor without importing anything. Native
  internal Browser/chopped-range drags retain their previous behavior.

External drops use Copy, never Move. They use the existing managed importer, so
original files stay where they are. Importing and assigning are separate undo
entries; a multiple-file drop is not advertised as an atomic transaction. A decode
failure is reported by the normal import dialog; valid files in the batch may
still be imported/assigned, and the final status reports the number placed.
The ZIP, whole-folder, mixed unsupported, remote-URL and oversized selections are
not treated as audio drops. Use + PACK for a whole extracted folder. External
file-manager drops on the Arrange tab are not part of this change.

## Validation and limits

2026-09-07 local validation: 17 new file-drop/pack tests plus the 49 existing sample
instrument tests passed in one completed run (66). The existing Browser/workspace
suite passed separately (53). Ruff checks and formatting of changed Python files
passed. Local environment: Python 3.13.5 / PySide6 6.8.3; it differs from the
repository's locked CI environment. These are focused checks, not full release or
physical audio-device certification. No audio engine or project schema changes.
Large-file import and pack indexing remain foreground operations, so a large pack
can temporarily occupy the UI. No third-party sample pack is redistributed here.

References: [Info-ZIP manual](https://manpages.debian.org/bookworm/unzip/unzip.1.en.html),
[Python ZIP CLI](https://docs.python.org/3/library/zipfile.html#command-line-interface),
[Qt MIME URLs](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QMimeData.html).
