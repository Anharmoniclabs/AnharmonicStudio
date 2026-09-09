#!/usr/bin/env python
"""Compile and validate native release candidates. Never publish or launch the GUI."""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.build_native import build
from scripts.build_source_bundle import build_bundle

ROOT = Path(__file__).resolve().parent.parent
NAME = "AnharmonicStudio"
PACKAGES = (
    "numpy",
    "PySide6",
    "PySide6_Addons",
    "PySide6_Essentials",
    "shiboken6",
    "sounddevice",
    "soundfile",
    "cffi",
    "pycparser",
)


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def check(executable, directory, report):
    result = run(
        [executable, "--self-check"],
        cwd=directory,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", OPENBLAS_NUM_THREADS="1"),
        capture_output=True,
        text=True,
        timeout=240,
    )
    report.write_text(result.stdout + result.stderr, encoding="utf-8")
    # A successful exit is insufficient if a broken GUI entry bypasses the test.
    if '"subprocess_export": true' not in result.stdout:
        raise RuntimeError(f"Self-check did not report a successful export: {result.stdout}")


def notices(bundle):
    for name in ("LICENSE", "THIRD_PARTY.md"):
        shutil.copy2(ROOT / name, bundle / name)
    for name in PACKAGES:
        distribution = metadata.distribution(name)
        target = bundle / "notices" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "METADATA.txt").write_text(
            distribution.read_text("METADATA") or "", encoding="utf-8"
        )
        for file in distribution.files or ():
            path = Path(str(file))
            if path.is_absolute() or ".." in path.parts:
                continue
            if any(word in file.name.lower() for word in ("license", "copying", "notice")):
                source = distribution.locate_file(file)
                if source.is_file():
                    destination = target / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)


def make_spec(stage, native, ffmpeg, ffprobe):
    windows, mac = sys.platform == "win32", sys.platform == "darwin"
    binaries = [(str(native), ".native"), (str(ffmpeg), "tools"), (str(ffprobe), "tools")]
    datas = [(str(ROOT / "assets"), "assets"), (str(ROOT / "mpclab/native/dsp.c"), "mpclab/native")]
    excluded = [
        "torch",
        "torchaudio",
        "demucs",
        "scipy",
        "matplotlib",
        "tkinter",
        "pytest",
        "IPython",
    ]
    icon = str(ROOT / "assets/branding/anharmonic-studios.png") if windows or mac else None
    specification = f"""
from PyInstaller.utils.hooks import copy_metadata
metadata = []
for package in {PACKAGES!r}:
    metadata += copy_metadata(package)
a = Analysis([{str(ROOT / "scripts/frozen_entry.py")!r}], pathex=[{str(ROOT)!r}],
    binaries={binaries!r}, datas={datas!r} + metadata, excludes={excluded!r})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name={NAME!r},
    console={not (windows or mac)!r}, strip=False, upx=False, icon={icon!r})
"""
    executables = "exe"
    if windows:
        specification += f"""
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name={NAME + "-worker"!r},
    console=True, strip=False, upx=False, icon={icon!r})
"""
        executables += ", worker"
    specification += f"coll = COLLECT({executables}, a.binaries, a.datas, strip=False, upx=False, name={NAME!r})\n"
    if mac:
        specification += f"""
app = BUNDLE(coll, name={NAME + ".app"!r}, icon={icon!r},
    bundle_identifier="com.anharmoniclabs.studio",
    info_plist={{"NSMicrophoneUsageDescription": "Record vocals and instruments into your songs.",
                "NSHighResolutionCapable": True}})
"""
    spec = stage / "release.spec"
    spec.write_text(specification, encoding="utf-8")
    return spec


def executable_in(bundle):
    if sys.platform == "win32":
        return bundle / (NAME + "-worker.exe")
    if sys.platform == "darwin":
        return bundle / "Contents/MacOS" / NAME
    return bundle / NAME


def package_windows(bundle, stage, output, version):
    compiler = (
        shutil.which("ISCC")
        or Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        / "Inno Setup 6/ISCC.exe"
    )
    if not Path(compiler).is_file():
        raise RuntimeError("Install Inno Setup 6 to create and test the Windows installer")
    installer = stage / "installer.iss"
    installer.write_text(
        f'''
[Setup]
AppId=AnharmonicLabs.Studio
AppName=Anharmonic Studios
AppVersion={version}
AppPublisher=Anharmonic Labs
DefaultDirName={{localappdata}}\\Programs\\AnharmonicStudio
DefaultGroupName=Anharmonic Studios
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={output}
OutputBaseFilename=AnharmonicStudio-{version}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={{app}}\\AnharmonicStudio.exe
LicenseFile={ROOT / "LICENSE"}
[Files]
Source: "{bundle}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{{group}}\\Anharmonic Studios"; Filename: "{{app}}\\AnharmonicStudio.exe"
''',
        encoding="utf-8",
    )
    run([compiler, installer])
    setup = next(output.glob("*-setup.exe"))
    installed = stage / "installed path with spaces"
    run(
        [
            setup,
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/DIR={installed}",
            f"/LOG={output / 'installer.log'}",
        ],
        timeout=180,
    )
    check(executable_in(installed), stage, output / "installed-self-check.log")
    run([installed / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
    if (installed / (NAME + ".exe")).exists():
        raise RuntimeError("Windows uninstaller left the application executable behind")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new output directory")
    parser.add_argument("--version", default="0.1.0-rc.1")
    args = parser.parse_args()
    if any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
        for c in args.version
    ):
        parser.error("Version must contain only letters, digits, dots or hyphens")
    if sys.platform not in ("win32", "darwin", "linux"):
        parser.error("Use Windows, macOS or Linux to build for that operating system")
    if metadata.version("pyinstaller") != "6.16.0":
        parser.error("Install requirements-build.txt in the build environment")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"Output already exists: {output}")
    native = build()
    media = [shutil.which(name) for name in ("ffmpeg", "ffprobe")]
    if not all(media):
        parser.error("Install FFmpeg and ffprobe on the build runner")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-build-", dir=output.parent) as temporary:
        stage = Path(temporary)
        ready = stage / "ready"
        ready.mkdir()
        spec = make_spec(stage, native, *media)
        run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                stage / "dist",
                "--workpath",
                stage / "work",
                spec,
            ],
            cwd=ROOT,
        )
        bundle = stage / "dist" / (NAME + (".app" if sys.platform == "darwin" else ""))
        check(executable_in(bundle), stage, ready / "self-check.log")
        relocated = stage / "relocated path with spaces" / bundle.name
        shutil.copytree(bundle, relocated, symlinks=True)
        check(executable_in(relocated), stage, ready / "relocated-self-check.log")
        # macOS signatures cover bundle resources: keep release notices/source
        # adjacent to the signed .app instead of mutating it after freezing.
        notices(ready)
        source_hash = build_bundle(ROOT, ready / f"{NAME}-{args.version}-source.zip")
        manifest = dict(
            version=args.version,
            source_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            platform=platform.platform(),
            architecture=platform.machine(),
            python=platform.python_version(),
            pyinstaller=metadata.version("pyinstaller"),
            packages={name: metadata.version(name) for name in PACKAGES},
            source_sha256=source_hash,
            signed_release=False,
            optional_stem_separation=False,
            physical_audio_tested=False,
            checks=[
                "native-DSP",
                "offscreen-UI",
                "project-roundtrip",
                "subprocess-WAV-export",
                "FFmpeg-resampling",
                "relocation",
            ],
        )
        for tool in media:
            result = run([tool, "-version"], capture_output=True, text=True)
            (ready / (Path(tool).stem + "-build.txt")).write_text(result.stdout, encoding="utf-8")
        (ready / "README.txt").write_text(
            f"Anharmonic Studios {args.version} — unsigned release candidate\n\n"
            "Paid official binary candidate; do not upload installers to public Releases.\n"
            "Python, Qt, native DSP and FFmpeg are bundled. Keep notices and matching source.\n"
            "Windows: use the setup EXE. macOS: copy the app from the DMG to Applications.\n"
            "Linux: extract the tar.gz; run ./AnharmonicStudio or ./AnharmonicStudio --install.\n"
            "User songs are stored outside the installed application. No automatic library migration.\n"
            "Code signing/notarization and physical microphone/output acceptance remain pending.\n"
            "Optional neural stem separation is a separate source installation.\n",
            encoding="utf-8",
        )
        if sys.platform == "win32":
            package_windows(bundle, stage, ready, args.version)
            manifest["checks"].extend(["installer", "installed-export", "uninstaller"])
        elif sys.platform == "darwin":
            image_root = stage / "dmg-root"
            image_root.mkdir()
            shutil.copytree(bundle, image_root / bundle.name, symlinks=True)
            (image_root / "Applications").symlink_to("/Applications")
            dmg = ready / f"{NAME}-{args.version}-macos-{platform.machine()}.dmg"
            run(
                [
                    "hdiutil",
                    "create",
                    "-volname",
                    "Anharmonic Studios",
                    "-srcfolder",
                    image_root,
                    "-format",
                    "UDZO",
                    dmg,
                ]
            )
            run(["hdiutil", "verify", dmg])
            manifest["checks"].append("DMG-integrity")
        else:
            notices(bundle)
            (bundle / "build-info.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            prefix = stage / "installer test"
            run([bundle / NAME, "--install", "--install-prefix", prefix], timeout=120)
            installed = next((prefix / "opt/anharmonic-studio").glob("build-*/AnharmonicStudio"))
            check(installed, stage, ready / "installed-self-check.log")
            manifest["checks"].extend(["installer", "installed-export"])
            with tarfile.open(
                ready / f"{NAME}-{args.version}-linux-{platform.machine()}.tar.gz", "w:gz"
            ) as archive:
                archive.add(bundle, arcname=NAME)
        (ready / "build-info.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        hashes = []
        for path in sorted(ready.rglob("*")):
            if path.is_file():
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                hashes.append(f"{digest}  {path.relative_to(ready).as_posix()}")
        (ready / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="utf-8")
        os.replace(ready, output)
    print(f"Validated {sys.platform} candidate: {output}")


if __name__ == "__main__":
    main()
