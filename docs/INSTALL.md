# Installation

## Local Linux application bundle

The Linux bundle includes Python, Qt, NumPy, the audio bindings and the compiled
DSP helper. Extract its entire folder and run `./AnharmonicStudio`; no Python,
uv or compiler is needed on the target. Install **FFmpeg** (including ffprobe)
through the system package manager. System graphics/audio drivers and a
compatible glibc remain necessary.

For a user installation and application-menu entry, run:

```bash
./AnharmonicStudio --install
```

This copies the application to a new folder under
`~/.local/opt/anharmonic-studio/` and updates its menu entry. It does not launch
the UI. Older builds remain available, and songs are stored separately under
`$XDG_DATA_HOME/anharmonic-studio`, normally
`~/.local/share/anharmonic-studio`. A failed installation leaves the previous
launcher intact. To undo an installation, remove its installed build folder and
`~/.local/share/applications/anharmonic-studio.desktop`; keep your song folder.

Existing source-checkout songs are not moved automatically. To use them, launch
the bundle with `--data-dir /absolute/path/to/your/checkout`. Keep the existing
`library/`, `projects/` and `exports/` together.

`./AnharmonicStudio --self-check` creates a disposable offscreen workstation,
saves and reloads a project, runs the real export worker and FFmpeg conversion,
and checks window cleanup. It opens no audio devices and changes no user
settings or songs.

The local candidate was built on CachyOS x86-64 with glibc 2.44; it is **not a
universal Linux download**. Consult its `build-info.json`. The Ubuntu 24.04 CI
build is configured separately and needs a successful remote run before claiming
Ubuntu compatibility. Optional AI stem separation currently uses the source
installation below.

## Source installation

The currently validated platform is Linux, Python 3.12, and 48 kHz audio.
Install Git and uv, then the platform dependencies below. Run these commands
from a terminal; launch the UI only when you are ready to use it.

Ubuntu 24.04:

```bash
sudo apt-get install git build-essential ffmpeg libegl1 libgl1 libopengl0 libxkbcommon0 libsndfile1 libportaudio2
```

Arch / CachyOS:

```bash
sudo pacman -S --needed git base-devel ffmpeg portaudio libsndfile libglvnd libxkbcommon
```

Clone and run:

```bash
git clone https://github.com/Anharmoniclabs/AnharmonicStudio.git
cd AnharmonicStudio
./run.sh
```

The launcher installs the locked Python environment and builds the native C DSP
helper. If no compiler is available, it reports the fallback; larger audio
buffers may then be necessary. Start with the default 512-frame audio profile.

Optional stem separation uses `./install-separation.sh` and downloads model
weights on first use. It is separate from the core application dependencies.

For development and headless validation:

```bash
uv sync --locked --group dev
bash scripts/quality.sh
```

The default test runner fails above 1536 MiB process RSS. It uses Linux `/proc`
monitoring and never terminates applications outside its own pytest child.

The separate experimental native engine requires CMake, pkg-config and the
PortAudio, ALSA and Lilv development packages. GitHub CI builds it, but the GUI
currently uses the Python engine with its native C DSP helper. Building the
experimental engine does not select it as the GUI backend.

Keep `library/`, `projects/`, and `exports/` with this checkout. The library's
`_cache/` directory backs temporary audio mappings; it must have disk space.
The default decoded cache uses up to 256 MiB of heap plus reclaimable mapped
pages. Active voices, DSP, Qt, and offline processing use additional memory.
Mapping avoids permanently retaining every decoded clip in heap; it does not
promise zero disk page faults during playback.

Signed Windows/macOS installers and the paid download backend remain release work
under the distribution policy. The local Linux candidate is unsigned.
