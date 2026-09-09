"""Installed applications keep songs, worker execution and system tools separate."""

import os
import sys

import pytest

from mpclab import runtime_paths
from mpclab.install_bundle import desktop_quote, install_bundle


def test_frozen_data_lives_outside_replaceable_application(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    assert runtime_paths.data_root() == tmp_path / "user-data/anharmonic-studio"
    assert runtime_paths.data_root(tmp_path / "existing") == tmp_path / "existing"
    monkeypatch.setenv("XDG_DATA_HOME", "relative-path-is-not-valid-xdg")
    assert runtime_paths.data_root().is_absolute()
    monkeypatch.setattr(sys, "frozen", False)
    assert runtime_paths.data_root() == runtime_paths.RESOURCE_ROOT


def test_export_worker_reenters_frozen_binary_without_starting_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    specification = tmp_path / "space and $dollar/job.json"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runtime_paths.export_command(specification) == [
        sys.executable,
        "--export-worker",
        str(specification),
    ]


@pytest.mark.parametrize("host", ("darwin", "win32"))
def test_native_platform_user_data_survives_application_updates(host, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", host)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local AppData"))
    root = runtime_paths.data_root()
    if host == "win32":
        assert root == tmp_path / "Local AppData/anharmonic-studio"
        worker = runtime_paths.export_command(tmp_path / "job.json")
        assert worker[0].endswith("AnharmonicStudio-worker.exe")
        assert worker[1:] == ["--export-worker", str(tmp_path / "job.json")]
    else:
        assert root.parts[-3:] == ("Library", "Application Support", "anharmonic-studio")
    assert runtime_paths.data_root(tmp_path / "chosen") == tmp_path / "chosen"


def test_media_tools_prefer_bundled_executables(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, "RESOURCE_ROOT", tmp_path)
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda name: f"/system/{name}")
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime_paths.media_tool("ffmpeg") == "/system/ffmpeg"
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/ffmpeg.exe").write_bytes(b"bundled")
    assert runtime_paths.media_tool("ffmpeg") == str(tmp_path / "tools/ffmpeg.exe")


def test_bundled_ffmpeg_retains_its_frozen_library_search_path(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, "RESOURCE_ROOT", tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path))
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert runtime_paths.external_environment(tmp_path / "tools/ffmpeg")["LD_LIBRARY_PATH"] == str(
        tmp_path
    )
    assert "LD_LIBRARY_PATH" not in runtime_paths.external_environment("/usr/bin/ffmpeg")


@pytest.mark.parametrize("original", [None, "/system/libs"])
def test_system_programs_do_not_inherit_bundled_libraries(original, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundled/qt")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    if original is not None:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)
    assert runtime_paths.external_environment().get("LD_LIBRARY_PATH") == original
    assert os.environ["LD_LIBRARY_PATH"] == "/bundled/qt"


def bundle_fixture(root):
    root.mkdir()
    (root / "AnharmonicStudio").write_bytes(b"fake executable")
    (root / "build-info.json").write_text("{}")
    return root


def test_install_update_keeps_previous_build_and_music(tmp_path):
    source = bundle_fixture(tmp_path / "bundle")
    prefix = tmp_path / "user prefix"
    music = prefix / "share/anharmonic-studio/projects/song.json"
    music.parent.mkdir(parents=True)
    music.write_text("precious song")
    first, desktop = install_bundle(source, prefix)
    (source / "AnharmonicStudio").write_bytes(b"updated executable")
    second, updated_desktop = install_bundle(source, prefix)
    assert second != first and desktop == updated_desktop
    assert (first / "AnharmonicStudio").read_bytes() == b"fake executable"
    assert (second / "AnharmonicStudio").read_bytes() == b"updated executable"
    assert "Exec=" + desktop_quote(second / "AnharmonicStudio") + " %f\n" in desktop.read_text()
    assert music.read_text() == "precious song"


def test_failed_install_keeps_working_launcher(tmp_path, monkeypatch):
    source = bundle_fixture(tmp_path / "bundle")
    prefix = tmp_path / "user"
    first, desktop = install_bundle(source, prefix)
    previous = desktop.read_bytes()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        install_bundle(source, prefix)
    assert desktop.read_bytes() == previous
    assert list(first.parent.iterdir()) == [first]


def test_installer_rejects_recursive_copy_and_external_links(tmp_path):
    source = bundle_fixture(tmp_path / "bundle")
    with pytest.raises(ValueError, match="inside"):
        install_bundle(source, source / "install")
    (source / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        install_bundle(source, tmp_path / "user")


@pytest.mark.parametrize("resource_folder", ["_internal", "."])
def test_installed_launcher_icon_points_into_its_build(tmp_path, resource_folder):
    source = bundle_fixture(tmp_path / "bundle")
    icon = source / resource_folder / "assets/branding/anharmonic-studios.svg"
    icon.parent.mkdir(parents=True)
    icon.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    installed, desktop = install_bundle(source, tmp_path / "user prefix")
    expected = installed / resource_folder / "assets/branding/anharmonic-studios.svg"
    assert "Icon=" + str(expected).replace("\\", "\\\\") + "\n" in desktop.read_text()
    assert expected.read_bytes() == icon.read_bytes()
