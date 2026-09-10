"""Unsigned distribution must ship its guide alongside checksum-bound notices."""

from scripts import build_release


def test_release_notices_include_exact_unsigned_installation_guide(tmp_path, monkeypatch):
    monkeypatch.setattr(build_release, "PACKAGES", ())
    build_release.notices(tmp_path)
    guide = (build_release.ROOT / "packaging/INSTALLATION.txt").read_bytes()
    assert (tmp_path / "INSTALLATION.txt").read_bytes() == guide
    assert (tmp_path / "LICENSE").is_file()
    assert (tmp_path / "THIRD_PARTY.md").is_file()
    for phrase in (
        b"UNSIGNED",
        b"WINDOWS",
        b"MAC",
        b"LINUX",
        b"SHA-256",
        b"Do not bypass",
    ):
        # Guidance must cover every target and explain checksum/security limits.
        assert phrase in guide.replace(b"\n", b" ")
