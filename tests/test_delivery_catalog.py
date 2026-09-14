"""Release preparation must reject mixed, mutable or unsupported artifacts."""

import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from delivery import prepare_catalog


VERSION = "1.0.0-rc.3"
COMMIT = "a" * 40
PLATFORMS = {
    "windows-x86_64": ("Windows-10-AMD64", "AMD64", "windows-x86_64-setup.exe"),
    "macos-arm64": ("macOS-15-arm64", "arm64", "macos-arm64.dmg"),
    "macos-x86_64": ("macOS-15-x86_64", "x86_64", "macos-x86_64.dmg"),
    "linux-x86_64": ("Linux-5-x86_64", "x86_64", "linux-x86_64.tar.gz"),
}


def source_zip(members=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in members or [("AnharmonicStudio/main.py", b"print('fixture')\n")]:
            archive.writestr(name, value)
    return output.getvalue()


def seal(candidate):
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(candidate).as_posix()}"
        for path in sorted(candidate.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (candidate / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def edit_json(path, **updates):
    data = json.loads(path.read_text())
    data.update(updates)
    path.write_text(json.dumps(data))


@pytest.fixture
def candidates(tmp_path):
    source = source_zip()
    for directory, (system, architecture, suffix) in PLATFORMS.items():
        candidate = tmp_path / directory
        candidate.mkdir()
        (candidate / f"AnharmonicStudio-{VERSION}-{suffix}").write_bytes(directory.encode())
        (candidate / f"AnharmonicStudio-{VERSION}-source.zip").write_bytes(source)
        (candidate / "LICENSE").write_text("Fixture license")
        (candidate / "notices").mkdir()
        (candidate / "notices/dependency.txt").write_text("Fixture dependency notice")
        checks = [
            "native-DSP",
            "offscreen-UI",
            "project-roundtrip",
            "subprocess-WAV-export",
            "FFmpeg-resampling",
            "relocation",
            "DMG-integrity",
            "installer",
            "installed-export",
            "uninstaller",
        ]
        (candidate / "build-info.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "source_commit": COMMIT,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "platform": system,
                    "architecture": architecture,
                    "signed_release": False,
                    "physical_audio_tested": False,
                    "checks": checks,
                }
            )
        )
        report = {
            "native_dsp": "Fixture native engine",
            "isolated_plugin_runtime": True,
            "project_roundtrip": True,
            "subprocess_export": True,
            "ffmpeg": True,
            "ui_lifetime": True,
        }
        for filename in ("self-check.log", "relocated-self-check.log", "installed-self-check.log"):
            (candidate / filename).write_text(json.dumps(report))
        seal(candidate)
    return tmp_path


def assert_no_outputs(root):
    assert not (root / "catalog.json").exists()
    assert not list(root.glob("*-source-and-notices.zip"))
    assert not list(root.glob(".catalog-stage-*"))


def add_acceptance(root):
    for directory, (_, _, suffix) in PLATFORMS.items():
        candidate = root / directory
        info = candidate / "build-info.json"
        edit_json(info, physical_audio_tested=True, distribution_policy="unsigned")
        evidence = json.loads(info.read_text())
        installer = candidate / f"AnharmonicStudio-{VERSION}-{suffix}"
        evidence.update(
            {
                "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "reviewed_by": "Fixture reviewer",
                "reviewed_at": "2020-01-01T00:00:00Z",
                "unsigned_distribution_accepted": True,
                "installation_guidance": "INSTALLATION.txt",
            }
        )
        checks = ["native-regressions", "paced-session", "physical-audio", "corresponding-source"]
        checks += ["unsigned-installation"]
        (candidate / "INSTALLATION.txt").write_text("Fixture instructions for unsigned packages")
        evidence["checks"] = {}
        (candidate / "acceptance").mkdir()
        for check in checks:
            report = f"acceptance/{check}.txt"
            (candidate / report).write_text(f"Synthetic {check} evidence, never real certification")
            evidence["checks"][check] = {"status": "passed", "report": report}
        (candidate / "release-acceptance.json").write_text(json.dumps(evidence))
        seal(candidate)


def test_candidate_materials_keep_exact_sources_notices_and_no_installer(candidates):
    output = prepare_catalog.prepare(candidates)
    catalog = json.loads(output.read_text())
    assert catalog["acceptance"] == "candidate"
    assert catalog["source_commit"] == COMMIT
    assert catalog["release"] == VERSION
    assert set(catalog["artifacts"]) == {"windows", "mac-arm", "mac-intel", "linux"}
    for artifact in catalog["artifacts"].values():
        installer = candidates / artifact["file"]
        materials = candidates / artifact["materials"]["file"]
        assert artifact["sha256"] == hashlib.sha256(installer.read_bytes()).hexdigest()
        assert artifact["materials"]["sha256"] == hashlib.sha256(materials.read_bytes()).hexdigest()
        with zipfile.ZipFile(materials) as archive:
            assert installer.name not in archive.namelist()
            assert archive.read("LICENSE") == b"Fixture license"
            assert archive.read("notices/dependency.txt") == b"Fixture dependency notice"
            source = archive.read(f"AnharmonicStudio-{VERSION}-source.zip")
            assert hashlib.sha256(source).hexdigest() == catalog["source_sha256"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_commit", "b" * 40),
        ("architecture", "arm64"),
        ("source_sha256", "b" * 64),
        ("signed_release", "false"),
        ("checks", []),
    ],
)
def test_rejects_mixed_or_invalid_build_metadata_before_output(candidates, field, value):
    candidate = candidates / "linux-x86_64"
    edit_json(candidate / "build-info.json", **{field: value})
    seal(candidate)
    with pytest.raises(ValueError):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_same_commit_with_different_source_is_rejected(candidates):
    candidate = candidates / "linux-x86_64"
    source = source_zip([("AnharmonicStudio/main.py", b"changed source")])
    (candidate / f"AnharmonicStudio-{VERSION}-source.zip").write_bytes(source)
    edit_json(candidate / "build-info.json", source_sha256=hashlib.sha256(source).hexdigest())
    seal(candidate)
    with pytest.raises(ValueError, match="same release, source commit and source"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


@pytest.mark.parametrize("name", ["build-info.json", "self-check.log"])
def test_manifest_and_reports_must_be_checksum_covered(candidates, name):
    checksum = candidates / "windows-x86_64/SHA256SUMS"
    checksum.write_text(
        "\n".join(
            line for line in checksum.read_text().splitlines() if not line.endswith("  " + name)
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="checksum-covered"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


@pytest.mark.parametrize(
    "name", ["../outside", "/tmp/escape", "a/../LICENSE", "a\\b", "C:escape", "CON", "a//b"]
)
def test_unsafe_checksum_paths_are_rejected(candidates, name):
    checksum = candidates / "windows-x86_64/SHA256SUMS"
    checksum.write_text(checksum.read_text() + f"{'a' * 64}  {name}\n")
    with pytest.raises(ValueError, match="Unsafe release path"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_duplicate_checksum_path_is_rejected(candidates):
    checksum = candidates / "windows-x86_64/SHA256SUMS"
    checksum.write_text(checksum.read_text() + checksum.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="Duplicate checksum"):
        prepare_catalog.prepare(candidates)


def test_symlink_input_is_rejected_even_when_content_matches(candidates):
    candidate = candidates / "linux-x86_64"
    notice = candidate / "LICENSE"
    notice.unlink()
    notice.symlink_to(candidates / "windows-x86_64/LICENSE")
    with pytest.raises(ValueError, match="symlink"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_extra_installer_is_not_accidentally_published_in_materials(candidates):
    candidate = candidates / "linux-x86_64"
    (candidate / "notices/old.exe").write_bytes(b"private binary")
    seal(candidate)
    with pytest.raises(ValueError, match="exactly one named installer"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


@pytest.mark.parametrize(
    "members",
    [
        [("AnharmonicStudio/../escape", b"bad")],
        [("Other/source.py", b"bad")],
        [("AnharmonicStudio/a", b"one"), ("AnharmonicStudio/A", b"two")],
        [("AnharmonicStudio/a", b"one"), ("AnharmonicStudio/a/b", b"two")],
    ],
)
def test_unsafe_or_ambiguous_source_archive_is_rejected(candidates, members):
    candidate = candidates / "windows-x86_64"
    source = source_zip(members)
    (candidate / f"AnharmonicStudio-{VERSION}-source.zip").write_bytes(source)
    edit_json(candidate / "build-info.json", source_sha256=hashlib.sha256(source).hexdigest())
    seal(candidate)
    with pytest.raises(ValueError):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_source_archive_symlink_is_rejected(candidates):
    member = zipfile.ZipInfo("AnharmonicStudio/link")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    source = source_zip([(member, b"/tmp/external")])
    candidate = candidates / "windows-x86_64"
    (candidate / f"AnharmonicStudio-{VERSION}-source.zip").write_bytes(source)
    edit_json(candidate / "build-info.json", source_sha256=hashlib.sha256(source).hexdigest())
    seal(candidate)
    with pytest.raises(ValueError, match="link, special or encrypted"):
        prepare_catalog.prepare(candidates)


def test_failed_self_check_cannot_be_overruled_by_manifest_checks(candidates):
    candidate = candidates / "linux-x86_64"
    edit_json(candidate / "installed-self-check.log", subprocess_export=False)
    seal(candidate)
    with pytest.raises(ValueError, match="Application report did not pass"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_input_mutation_during_preparation_never_publishes_catalog(candidates, monkeypatch):
    original = zipfile.ZipFile.open
    modified = False

    def changing_open(archive, name, mode="r", *args, **kwargs):
        nonlocal modified
        if mode == "w" and not modified:
            modified = True
            candidate = candidates / "linux-x86_64"
            (candidate / "LICENSE").write_text("Changed after verification")
            seal(candidate)
        return original(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", changing_open)
    with pytest.raises(ValueError, match="changed"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


@pytest.mark.parametrize("name", ["catalog.json", "linux-source-and-notices.zip"])
def test_existing_outputs_are_never_replaced(candidates, name):
    target = candidates / name
    target.write_bytes(b"existing delivery record")
    with pytest.raises(ValueError, match="already exist"):
        prepare_catalog.prepare(candidates)
    assert target.read_bytes() == b"existing delivery record"


def test_publication_failure_cleans_only_its_new_outputs(candidates, monkeypatch):
    original = prepare_catalog.os.link

    def fail_catalog(source, target):
        if Path(target).name == "catalog.json":
            raise OSError("fixture publication failure")
        return original(source, target)

    monkeypatch.setattr(prepare_catalog.os, "link", fail_catalog)
    with pytest.raises(OSError, match="publication failure"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)
    assert (candidates / "linux-x86_64/build-info.json").exists()


def test_production_rejects_candidates_without_physical_acceptance(candidates):
    with pytest.raises(ValueError, match="physical_audio_tested"):
        prepare_catalog.prepare(candidates, production=True)
    assert_no_outputs(candidates)


def test_production_requires_evidence_in_addition_to_boolean_flags(candidates):
    for directory in PLATFORMS:
        candidate = candidates / directory
        edit_json(
            candidate / "build-info.json",
            physical_audio_tested=True,
            distribution_policy="unsigned",
        )
        seal(candidate)
    with pytest.raises(ValueError, match="release-acceptance.json"):
        prepare_catalog.prepare(candidates, production=True)


def test_production_catalog_records_explicit_reviewed_acceptance(candidates):
    add_acceptance(candidates)
    catalog = json.loads(prepare_catalog.prepare(candidates, production=True).read_text())
    assert catalog["acceptance"] == "production"
    assert catalog["distribution_policy"] == "unsigned"
    with zipfile.ZipFile(
        candidates / catalog["artifacts"]["windows"]["materials"]["file"]
    ) as archive:
        assert "release-acceptance.json" in archive.namelist()
        assert "acceptance/physical-audio.txt" in archive.namelist()


@pytest.mark.parametrize(
    "field,value",
    [
        ("installer_sha256", "0" * 64),
        ("source_commit", "b" * 40),
        ("reviewed_by", " "),
        ("reviewed_at", "2020-01-01"),
        ("reviewed_at", "2999-01-01T00:00:00Z"),
        ("checks", {}),
        ("distribution_policy", "signed"),
        ("unsigned_distribution_accepted", False),
        ("installation_guidance", "missing.txt"),
        ("installation_guidance", "LICENSE"),
    ],
)
def test_production_rejects_stale_or_unreviewed_acceptance(candidates, field, value):
    add_acceptance(candidates)
    candidate = candidates / "windows-x86_64"
    edit_json(candidate / "release-acceptance.json", **{field: value})
    seal(candidate)
    with pytest.raises(ValueError):
        prepare_catalog.prepare(candidates, production=True)
    assert_no_outputs(candidates)


def test_production_policy_rejects_inaccurate_notarization_claims(candidates):
    add_acceptance(candidates)
    candidate = candidates / "macos-arm64"
    edit_json(candidate / "build-info.json", notarized=True)
    seal(candidate)
    with pytest.raises(ValueError, match="unsigned distribution policy"):
        prepare_catalog.prepare(candidates, production=True)


def test_empty_physical_report_is_not_acceptance_evidence(candidates):
    add_acceptance(candidates)
    candidate = candidates / "linux-x86_64"
    (candidate / "acceptance/physical-audio.txt").write_text("")
    seal(candidate)
    with pytest.raises(ValueError, match="nonempty report"):
        prepare_catalog.prepare(candidates, production=True)
    assert_no_outputs(candidates)


def test_installation_guidance_must_disclose_unsigned_status(candidates):
    add_acceptance(candidates)
    candidate = candidates / "linux-x86_64"
    (candidate / "INSTALLATION.txt").write_text("Run the installer.")
    seal(candidate)
    with pytest.raises(ValueError, match="installation guidance"):
        prepare_catalog.prepare(candidates, production=True)
    assert_no_outputs(candidates)


def test_duplicate_manifest_fields_are_rejected(candidates):
    candidate = candidates / "windows-x86_64"
    manifest = candidate / "build-info.json"
    manifest.write_text(manifest.read_text()[:-1] + ', "version": "other"}')
    seal(candidate)
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_changed_installer_without_updated_checksums_is_rejected(candidates):
    candidate = candidates / "linux-x86_64"
    (candidate / f"AnharmonicStudio-{VERSION}-linux-x86_64.tar.gz").write_bytes(b"changed binary")
    with pytest.raises(ValueError, match="Release checksum failed"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_different_candidate_version_is_rejected(candidates):
    candidate = candidates / "linux-x86_64"
    for name in (
        f"AnharmonicStudio-{VERSION}-source.zip",
        f"AnharmonicStudio-{VERSION}-linux-x86_64.tar.gz",
    ):
        (candidate / name).rename(candidate / name.replace(VERSION, "2.0.0"))
    edit_json(candidate / "build-info.json", version="2.0.0")
    seal(candidate)
    with pytest.raises(ValueError, match="same release"):
        prepare_catalog.prepare(candidates)
    assert_no_outputs(candidates)


def test_concurrent_output_creation_is_preserved(candidates, monkeypatch):
    original = prepare_catalog.os.link

    def create_conflict(source, target):
        if Path(target).name == "catalog.json":
            Path(target).write_text("Catalog belonging to another preparation")
        return original(source, target)

    monkeypatch.setattr(prepare_catalog.os, "link", create_conflict)
    with pytest.raises(FileExistsError):
        prepare_catalog.prepare(candidates)
    assert (candidates / "catalog.json").read_text() == "Catalog belonging to another preparation"
    assert not list(candidates.glob("*-source-and-notices.zip"))
