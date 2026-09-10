"""Validate four immutable native candidates and prepare their delivery catalog.

The default produces a candidate catalog. Production preparation follows the
owner's unsigned-distribution policy: distribution_policy="unsigned",
signed_release=false and physical_audio_tested=true in every build-info.json.
Each target must include a checksum-covered release-acceptance.json with this schema:

    {"version": "1.0.0", "source_commit": "<40 hex>",
     "source_sha256": "<64 hex>", "installer_sha256": "<64 hex>",
     "distribution_policy": "unsigned", "unsigned_distribution_accepted": true,
     "installation_guidance": "INSTALLATION.txt",
     "reviewed_by": "release owner", "reviewed_at": "2026-09-10T17:00:00Z",
     "checks": {"native-regressions": {"status": "passed", "report": "qa.txt"},
                ...}}

Required report keys are native-regressions, paced-session, physical-audio,
corresponding-source and unsigned-installation. Every report must be a nonempty
checksum-covered file. Installation guidance must disclose the unsigned status.
The release owner must review the platform/device evidence and unsigned delivery,
then seal the final installer, manifest and evidence in SHA256SUMS. This checks
record integrity and attribution, not the truth of QA claims. It never signs,
notarizes, deploys or certifies.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

TARGETS = {
    "windows": ("windows-x86_64", "windows-x86_64-setup.exe"),
    "mac-arm": ("macos-arm64", "macos-arm64.dmg"),
    "mac-intel": ("macos-x86_64", "macos-x86_64.dmg"),
    "linux": ("linux-x86_64", "linux-x86_64.tar.gz"),
}
SOFTWARE_CHECKS = {
    "native-DSP",
    "offscreen-UI",
    "project-roundtrip",
    "subprocess-WAV-export",
    "FFmpeg-resampling",
    "relocation",
}
REPORT_CHECKS = {
    "isolated_plugin_runtime",
    "project_roundtrip",
    "subprocess_export",
    "ffmpeg",
    "ui_lifetime",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative_name(name):
    if not isinstance(name, str) or not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Unsafe release path")
    parts = name.split("/")
    if any(
        part in ("", ".", "..")
        or part != part.rstrip(" .")
        or any(c in part for c in '\\:<>"|?*')
        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise ValueError(f"Unsafe release path: {name}")
    return PurePosixPath(name)


def _plain_file(candidate, name):
    relative = _relative_name(name)
    path = candidate
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"Release inputs cannot be symlinks: {name}")
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Missing release file: {name}")
    return path


def _json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field in {path.name}: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def _snapshot(candidate):
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError(f"Expected a real candidate directory: {candidate.name}")
    checksums = _plain_file(candidate, "SHA256SUMS")
    sealed = {}
    aliases = set()
    raw = checksums.read_bytes()
    for line in raw.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"Malformed SHA256SUMS: {candidate.name}")
        expected, name = match.groups()
        _relative_name(name)
        if name.casefold() in aliases or name.casefold() == "sha256sums":
            raise ValueError(f"Duplicate checksum path: {name}")
        aliases.add(name.casefold())
        if digest(_plain_file(candidate, name)) != expected:
            raise ValueError(f"Release checksum failed: {candidate.name}/{name}")
        sealed[name] = expected
    actual = set()
    for path in candidate.rglob("*"):
        if path.is_symlink():
            raise ValueError("Release inputs cannot contain symlinks")
        if path.is_file():
            actual.add(path.relative_to(candidate).as_posix())
        elif not path.is_dir():
            raise ValueError("Release inputs must be regular files or directories")
    if actual != set(sealed) | {"SHA256SUMS"}:
        raise ValueError(f"Every release file must be checksum-covered: {candidate.name}")
    if "build-info.json" not in sealed:
        raise ValueError("build-info.json must be checksum-covered")
    sealed["SHA256SUMS"] = hashlib.sha256(raw).hexdigest()
    return sealed


def _source_archive(path):
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = set()
            file_names = set()
            files = 0
            for member in members:
                name = member.filename.rstrip("/") if member.is_dir() else member.filename
                relative = _relative_name(name)
                if relative.parts[0] != "AnharmonicStudio" or (
                    not member.is_dir() and len(relative.parts) < 2
                ):
                    raise ValueError("Source archive must contain the AnharmonicStudio root")
                if name.casefold() in names:
                    raise ValueError("Duplicate source archive member")
                if any(parent.as_posix().casefold() in file_names for parent in relative.parents):
                    raise ValueError("Source archive path is both a file and directory")
                if not member.is_dir() and any(
                    existing.startswith(name.casefold() + "/") for existing in names
                ):
                    raise ValueError("Source archive path is both a file and directory")
                names.add(name.casefold())
                if not member.is_dir():
                    file_names.add(name.casefold())
                mode = member.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
                    raise ValueError("Source archive contains a link, special or encrypted member")
                files += not member.is_dir()
            if not files or archive.testzip() is not None:
                raise ValueError("Source archive is empty or corrupt")
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Invalid source archive") from exc


def _production_evidence(candidate, sealed, info, installer):
    if info.get("physical_audio_tested") is not True:
        raise ValueError("Production requires physical_audio_tested=true with evidence")
    if (
        info.get("distribution_policy") != "unsigned"
        or info.get("signed_release") is not False
        or info.get("notarized", False) is not False
    ):
        raise ValueError("Production requires the explicit unsigned distribution policy")
    if "release-acceptance.json" not in sealed:
        raise ValueError("Production requires checksum-covered release-acceptance.json evidence")
    evidence = _json(candidate / "release-acceptance.json")
    for key in ("version", "source_commit", "source_sha256"):
        if evidence.get(key) != info[key]:
            raise ValueError(f"Acceptance evidence does not match build {key}")
    if evidence.get("installer_sha256") != sealed[installer]:
        raise ValueError("Acceptance evidence does not match final installer")
    if (
        evidence.get("distribution_policy") != "unsigned"
        or evidence.get("unsigned_distribution_accepted") is not True
    ):
        raise ValueError("Acceptance evidence must explicitly accept unsigned distribution")
    guidance = evidence.get("installation_guidance")
    if (
        not isinstance(guidance, str)
        or guidance not in sealed
        or PurePosixPath(guidance).suffix.lower() not in (".txt", ".md")
        or not re.search(
            r"\bunsigned\b", _plain_file(candidate, guidance).read_text(encoding="utf-8"), re.I
        )
    ):
        raise ValueError("Unsigned production requires checksum-covered installation guidance")
    if not isinstance(evidence.get("reviewed_by"), str) or not evidence["reviewed_by"].strip():
        raise ValueError("Acceptance evidence needs an identified reviewer")
    try:
        reviewed = datetime.fromisoformat(evidence["reviewed_at"])
        if reviewed.tzinfo is None or reviewed > datetime.now(timezone.utc):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Acceptance evidence needs a past timestamp with timezone") from exc
    required = {
        "native-regressions",
        "paced-session",
        "physical-audio",
        "corresponding-source",
        "unsigned-installation",
    }
    checks = evidence.get("checks")
    if not isinstance(checks, dict) or not required.issubset(checks):
        raise ValueError("Production acceptance is missing required verification reports")
    for kind in required:
        check = checks[kind]
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise ValueError(f"Production acceptance did not pass: {kind}")
        report = check.get("report")
        if (
            not isinstance(report, str)
            or report not in sealed
            or report in (installer, "SHA256SUMS", "build-info.json", "release-acceptance.json")
            or PurePosixPath(report).suffix.lower() not in (".txt", ".log", ".json")
            or not _plain_file(candidate, report).stat().st_size
        ):
            raise ValueError(f"Production requires a checksum-covered nonempty report: {kind}")


def _candidate(root, platform, directory, suffix, production):
    candidate = root / directory
    sealed = _snapshot(candidate)
    info = _json(candidate / "build-info.json")
    for key, pattern in (
        ("version", r"[A-Za-z0-9][A-Za-z0-9.-]{0,99}"),
        ("source_commit", r"[0-9a-f]{40}"),
        ("source_sha256", r"[0-9a-f]{64}"),
    ):
        if not isinstance(info.get(key), str) or not re.fullmatch(pattern, info[key]):
            raise ValueError(f"Invalid build manifest {key}: {directory}")
    for key in ("signed_release", "physical_audio_tested"):
        if type(info.get(key)) is not bool:
            raise ValueError(f"Build manifest needs a boolean {key}")
    architecture = str(info.get("architecture", "")).lower()
    expected_arch = {"arm64", "aarch64"} if platform == "mac-arm" else {"x86_64", "amd64"}
    os_prefix = {"windows": ("Windows-",), "linux": ("Linux-",)}.get(
        platform, ("macOS-", "Darwin-")
    )
    if architecture not in expected_arch or not str(info.get("platform", "")).startswith(os_prefix):
        raise ValueError(f"Build platform/architecture does not match target: {directory}")
    installer = f"AnharmonicStudio-{info['version']}-{suffix}"
    source = f"AnharmonicStudio-{info['version']}-source.zip"
    archives = {
        name
        for name in sealed
        if name.lower().endswith((".exe", ".dmg", ".tar.gz", ".zip", ".enc"))
    }
    if archives != {installer, source}:
        raise ValueError(f"Expected exactly one named installer and source archive: {directory}")
    if sealed[source] != info["source_sha256"]:
        raise ValueError("Source archive does not match build manifest source_sha256")
    _source_archive(candidate / source)
    required = SOFTWARE_CHECKS | (
        {"DMG-integrity"} if platform.startswith("mac-") else {"installer", "installed-export"}
    )
    if platform == "windows":
        required |= {"uninstaller"}
    checks = info.get("checks")
    if (
        not isinstance(checks, list)
        or not all(isinstance(c, str) for c in checks)
        or not required.issubset(checks)
    ):
        raise ValueError(f"Build manifest is missing required software checks: {directory}")
    reports = ["self-check.log", "relocated-self-check.log"]
    if not platform.startswith("mac-"):
        reports.append("installed-self-check.log")
    for name in reports:
        if name not in sealed:
            raise ValueError(f"Missing checksum-covered application report: {name}")
        report = _json(candidate / name)
        if any(report.get(key) is not True for key in REPORT_CHECKS) or not report.get(
            "native_dsp"
        ):
            raise ValueError(f"Application report did not pass: {directory}/{name}")
    if production:
        _production_evidence(candidate, sealed, info, installer)
    return candidate, sealed, info, installer


def prepare(root, *, production=False):
    root = Path(root).resolve(strict=True)
    names = [f"{platform}-source-and-notices.zip" for platform in TARGETS] + ["catalog.json"]
    if any(os.path.lexists(root / name) for name in names):
        raise ValueError("Catalog outputs already exist; use a fresh preparation directory")
    candidates = {
        platform: _candidate(root, platform, directory, suffix, production)
        for platform, (directory, suffix) in TARGETS.items()
    }
    identities = {
        (info["version"], info["source_commit"], info["source_sha256"])
        for _, _, info, _ in candidates.values()
    }
    if len(identities) != 1:
        raise ValueError("All four installers must have the same release, source commit and source")
    version, commit, source_hash = identities.pop()
    artifacts = {}
    with tempfile.TemporaryDirectory(prefix=".catalog-stage-", dir=root) as temporary:
        stage = Path(temporary)
        for platform, (candidate, sealed, _, installer) in candidates.items():
            materials = stage / f"{platform}-source-and-notices.zip"
            with zipfile.ZipFile(materials, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, expected in sorted(sealed.items()):
                    if name == installer:
                        continue
                    actual = hashlib.sha256()
                    with (
                        _plain_file(candidate, name).open("rb") as source,
                        archive.open(name, "w") as target,
                    ):
                        while chunk := source.read(1024 * 1024):
                            actual.update(chunk)
                            target.write(chunk)
                    if actual.hexdigest() != expected:
                        raise ValueError("Release inputs changed while preparing materials")
            artifacts[platform] = {
                "file": f"{candidate.name}/{installer}",
                "sha256": sealed[installer],
                "materials": {"file": materials.name, "sha256": digest(materials)},
            }
        catalog = {
            "release": version,
            "source_commit": commit,
            "source_sha256": source_hash,
            "acceptance": "production" if production else "candidate",
            "artifacts": artifacts,
        }
        if production:
            catalog["distribution_policy"] = "unsigned"
        (stage / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        for candidate, sealed, _, _ in candidates.values():
            if _snapshot(candidate) != sealed:
                raise ValueError("Release inputs changed before catalog publication")
        # Link complete outputs without replacing anything; publish the catalog last.
        created = []
        try:
            for name in names:
                destination = root / name
                os.link(stage / name, destination)
                created.append(destination)
        except OSError:
            for path in created:
                path.unlink()
            raise
    return root / "catalog.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--production", action="store_true", help="require reviewed release evidence"
    )
    args = parser.parse_args()
    try:
        print(prepare(args.directory, production=args.production))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Release preparation refused: {exc}\n")
