"""Install a Linux folder bundle without administrator access or opening windows."""

import os
from pathlib import Path
import shutil
import tempfile


def desktop_quote(value):
    # Desktop Entry Exec quoting differs from shell quoting. Percent is a field
    # code even inside quotes; double it before escaping the reserved characters.
    value = str(value).replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    # The whole desktop value has a second, general backslash-escape layer.
    return '"' + value.replace("\\", "\\\\") + '"'


def install_bundle(source, prefix):
    source, prefix = Path(source).resolve(), Path(prefix).expanduser().resolve()
    if not (source / "AnharmonicStudio").is_file() or not (source / "build-info.json").is_file():
        raise ValueError("Install requires a complete built Linux bundle")
    if prefix == source or prefix.is_relative_to(source):
        raise ValueError("Installation destination cannot be inside the source bundle")
    if any(c in str(prefix) for c in ("\n", "\r", "\t", "=")):
        raise ValueError("Installation path contains characters unsupported by desktop launchers")
    for path in source.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(source):
            raise ValueError(f"Bundle link points outside the bundle: {path}")
    versions = prefix / "opt/anharmonic-studio"
    versions.mkdir(parents=True, exist_ok=True)
    installed = Path(tempfile.mkdtemp(prefix="build-", dir=versions))
    temporary = None
    try:
        shutil.copytree(source, installed, dirs_exist_ok=True, symlinks=True)
        desktop_dir = prefix / "share/applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop = desktop_dir / "anharmonic-studio.desktop"
        content = (
            "[Desktop Entry]\nType=Application\nName=Anharmonic Studio\n"
            "Comment=Sampler, sequencer and music workstation\n"
            f"Exec={desktop_quote(installed / 'AnharmonicStudio')} %f\n"
            "Terminal=false\nCategories=AudioVideo;Audio;\nStartupNotify=false\n"
        )
        fd, temporary = tempfile.mkstemp(prefix=".anharmonic-", dir=desktop_dir)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, desktop)
        return installed, desktop
    except BaseException:
        # Only this installation's freshly allocated folder is removed. Previous
        # versions, the current launcher, and every song directory are preserved.
        shutil.rmtree(installed)
        raise
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
