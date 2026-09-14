"""Static backlog provenance, policy separation, and bounded literal-only imports."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from scripts import import_daw_backlog as importer


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "a" * 40
BASELINE = "e07bd34251b5724312b4df2b7e92a5f2b778a3b3"
COMPARISON_HEADER = [
    "ID",
    "Area",
    "Capability / deliverable",
    "Anharmonic",
    "Priority",
    "Current implementation",
    "Code evidence",
    "REAPER",
    "Nuendo",
]
DELIVERABLE_HEADER = [
    "ID",
    "Deliverable",
    "Source status",
    "Priority",
    "Current limitation",
    "Done when",
]
REQUIREMENTS = [
    (
        "01-01",
        "Tracks & independent routing",
        "Partial",
        "P0",
        "Eight tracks.",
        "Recall 128 tracks.",
    ),
    (
        "19-01",
        "Signed platform installers",
        "Acceptance pending",
        "P0",
        "Unsigned builds.",
        "Build signed packages.",
    ),
]


def _evidence(identifier):
    return f"https://github.com/example/daw/blob/{SNAPSHOT}/{identifier}.py"


def _markdown(requirements=REQUIREMENTS):
    text = f"Source snapshot: https://github.com/example/daw/tree/{SNAPSHOT}\n\n"
    for identifier, title, status, priority, limitation, acceptance in requirements:
        text += (
            f"- [ ] **{identifier} — {title}** · {status} · {priority}\n"
            f"  - Current source: {limitation}\n"
            f"  - Done when: {acceptance}\n"
            "  - Benchmark examples: RE, NU\n"
            f"  - [Relevant source architecture]({_evidence(identifier)})\n\n"
        )
    return text


def _comparison():
    rows = [COMPARISON_HEADER.copy()]
    for identifier, title, status, priority, limitation, _ in REQUIREMENTS:
        rows.append(
            [
                identifier,
                "Foundation",
                title,
                status,
                priority,
                limitation,
                _evidence(identifier),
                "Doc",
                "NV",
            ]
        )
    rows.append(
        [
            "01-02",
            "Foundation",
            "Existing transport",
            "Present",
            "Keep",
            "Works.",
            _evidence("01-02"),
            "Doc",
            "NV",
        ]
    )
    return rows


def _table_xml(rows):
    root = ET.Element("worksheet", xmlns=importer.NS["x"])
    data = ET.SubElement(root, "sheetData")
    for index, values in enumerate(rows, 1):
        row = ET.SubElement(data, "row", r=str(index))
        for column, value in enumerate(values):
            cell = ET.SubElement(row, "c", r=f"{chr(65 + column)}{index}", t="inlineStr")
            ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
    return ET.tostring(root, encoding="utf-8")


def _members(comparison=None, deliverables=None):
    workbook = ET.Element("workbook", xmlns=importer.NS["x"])
    sheets = ET.SubElement(workbook, "sheets")
    relationships = ET.Element("Relationships")
    rows = {
        "Comparison": comparison if comparison is not None else _comparison(),
        "Deliverables": deliverables
        if deliverables is not None
        else [DELIVERABLE_HEADER.copy(), *[list(item) for item in REQUIREMENTS]],
    }
    result = {}
    for index, (name, values) in enumerate(rows.items(), 1):
        ET.SubElement(
            sheets,
            "sheet",
            {
                "name": name,
                f"{{{importer.REL}}}id": f"rId{index}",
            },
        )
        ET.SubElement(
            relationships,
            "Relationship",
            {
                "Id": f"rId{index}",
                "Target": f"worksheets/sheet{index}.xml",
            },
        )
        result[f"xl/worksheets/sheet{index}.xml"] = _table_xml(values)
    result["xl/workbook.xml"] = ET.tostring(workbook)
    result["xl/_rels/workbook.xml.rels"] = ET.tostring(relationships)
    return result


def _inputs(
    tmp_path,
    *,
    comparison=None,
    deliverables=None,
    markdown=None,
    member_updates=None,
    extra_members=(),
):
    markdown_path, workbook_path = tmp_path / "audit.md", tmp_path / "comparison.xlsx"
    markdown_path.write_text(_markdown() if markdown is None else markdown, encoding="utf-8")
    members = _members(comparison, deliverables)
    members.update(member_updates or {})
    with zipfile.ZipFile(workbook_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in [*members.items(), *extra_members]:
            archive.writestr(name, data)
    return markdown_path, workbook_path


def test_checked_in_catalog_retains_exact_historical_baseline():
    catalog = json.loads((ROOT / "planning/daw-capabilities.json").read_text())
    assert catalog["schema_version"] == 1
    assert catalog["provenance"]["reviewed_source_commit"] == BASELINE
    assert catalog["provenance"]["markdown"] == {
        "name": "Anharmonic_Full_Deliverables.md",
        "sha256": "c33e4a5ac27715a169e5f1693f983ae9e422bdb9c19a2a19f3b76bee1d1eeb34",
    }
    assert catalog["provenance"]["workbook"] == {
        "name": "Anharmonic_DAW_Comparison.xlsx",
        "sha256": "ebe483e85ea5c79e820020366cfed369267dae1dd69b4fa12ad1b0e4efd1dc03",
    }
    capabilities = catalog["capabilities"]
    assert len(capabilities) == len({item["id"] for item in capabilities}) == 216
    packages = [item for item in capabilities if item["work_package"]]
    assert len(packages) == len({item["id"] for item in packages}) == 194
    statuses = {"Partial": 73, "Missing": 112, "Present": 22, "Acceptance pending": 9}
    assert Counter(item["baseline_status"] for item in capabilities) == statuses
    assert catalog["counts"] == {
        "capabilities": 216,
        "work_packages": 194,
        "baseline_status": statuses,
    }
    for item in capabilities:
        assert re.fullmatch(r"\d{2}-\d{2}", item["id"])
        assert BASELINE in item["source_evidence"]
        assert item["priority"] in {"P0", "P1", "P2", "P3", "Keep"}
        assert bool(item["acceptance"]) is item["work_package"]
    assert any("NV" in item["manufacturer_cells_as_supplied"].values() for item in capabilities)


def test_progress_references_real_work_packages_and_preserves_unsigned_override():
    catalog = json.loads((ROOT / "planning/daw-capabilities.json").read_text())
    progress = json.loads((ROOT / "planning/daw-progress.json").read_text())
    packages = {item["id"]: item for item in catalog["capabilities"] if item["work_package"]}
    assert progress["schema_version"] == 1
    assert re.fullmatch(r"[0-9a-f]{40}", progress["baseline_commit"])
    assert progress["default_work_status"] == "not_started"
    assert progress["items"].keys() <= packages.keys()
    for item in progress["items"].values():
        assert item["status"] in {
            "not_started",
            "in_progress",
            "implemented",
            "acceptance_pending",
            "policy_override",
            "blocked",
        }
        assert item.get("scope") or item.get("replacement_acceptance")
        for evidence in item.get("evidence", []):
            assert not Path(evidence).is_absolute()
            assert ".." not in Path(evidence).parts
            assert (ROOT / evidence).is_file(), evidence
    assert progress["policy"]["distribution"] == "unsigned"
    assert progress["policy"]["cloudflare_promotion"] == "held_until_verified_release_acceptance"
    override = progress["items"]["19-01"]
    assert override["status"] == "policy_override"
    assert "unsigned" in override["owner_instruction"].lower()
    for requirement in ("four", "unsigned", "checksums", "installation", "device"):
        assert requirement in override["replacement_acceptance"].lower()
    assert "sign" in packages["19-01"]["acceptance"].lower()
    assert packages["19-01"]["baseline_status"] == "Acceptance pending"


def test_synthetic_catalog_is_complete_literal_and_source_bound(tmp_path):
    markdown, workbook = _inputs(tmp_path)
    original = {path: path.read_bytes() for path in (markdown, workbook)}
    catalog = importer.build_catalog(markdown, workbook)
    assert catalog["provenance"]["reviewed_source_commit"] == SNAPSHOT
    for kind, path in (("markdown", markdown), ("workbook", workbook)):
        assert catalog["provenance"][kind] == {
            "name": path.name,
            "sha256": hashlib.sha256(original[path]).hexdigest(),
        }
        assert path.read_bytes() == original[path]
    assert catalog["counts"] == {
        "capabilities": 3,
        "work_packages": 2,
        "baseline_status": {"Partial": 1, "Acceptance pending": 1, "Present": 1},
    }
    tracks, installers, transport = catalog["capabilities"]
    assert tracks["title"] == "Tracks & independent routing"
    assert tracks["manufacturer_cells_as_supplied"] == {"REAPER": "Doc", "Nuendo": "NV"}
    assert tracks["acceptance"] == "Recall 128 tracks."
    assert installers["acceptance"] == "Build signed packages."
    assert transport["acceptance"] is None and transport["work_package"] is False


@pytest.mark.parametrize("column", range(1, 6))
def test_markdown_and_workbook_field_disagreement_is_rejected(tmp_path, column):
    rows = [DELIVERABLE_HEADER.copy(), *[list(item) for item in REQUIREMENTS]]
    rows[1][column] = "A different source value"
    paths = _inputs(tmp_path, deliverables=rows)
    with pytest.raises(ValueError, match="Source documents disagree for 01-01"):
        importer.build_catalog(*paths)


@pytest.mark.parametrize("column", [2, 3, 4])
def test_workbook_sheet_disagreement_is_rejected(tmp_path, column):
    rows = _comparison()
    rows[1][column] = "A different source value"
    paths = _inputs(tmp_path, comparison=rows)
    with pytest.raises(ValueError, match="Workbook sheets disagree for 01-01"):
        importer.build_catalog(*paths)


def test_missing_source_snapshot_and_mismatched_ids_are_rejected(tmp_path):
    paths = _inputs(tmp_path, markdown=_markdown(REQUIREMENTS[:1]))
    with pytest.raises(ValueError, match="deliverable IDs differ"):
        importer.build_catalog(*paths)
    paths = _inputs(tmp_path, comparison=[_comparison()[0], *_comparison()[2:]])
    with pytest.raises(ValueError, match="absent from comparison"):
        importer.build_catalog(*paths)
    paths = _inputs(tmp_path, markdown=_markdown().replace("Source snapshot:", "Source changed:"))
    with pytest.raises(ValueError, match="Missing source snapshot provenance"):
        importer.build_catalog(*paths)


@pytest.mark.parametrize(
    "rows",
    [
        [["ID", "Name"], ["01-01", "One"], ["01-01", "Duplicate"]],
        [["ID", "Name"], ["ID", "Name"], ["01-01", "One"]],
        [["ID", "ID"], ["01-01", "One"]],
        [["01-01", "Before header"], ["ID", "Name"]],
        [["ID"], ["01-01", "Extra column"]],
        [["Name"], ["No capability IDs"]],
    ],
)
def test_ambiguous_capability_tables_are_rejected(rows):
    with pytest.raises(ValueError):
        importer.table_records(rows)


def test_duplicate_and_unparsed_markdown_requirements_are_rejected():
    with pytest.raises(ValueError, match="Duplicate Markdown capability"):
        importer.markdown_requirements(_markdown() + _markdown())
    for text in (
        _markdown().replace("· P0", "· P4", 1),
        _markdown().replace("  - Done when:", "  - Missing field:", 1),
        _markdown().replace("architecture](https://", "architecture](http://", 1),
    ):
        with pytest.raises(ValueError, match="Some Markdown requirements"):
            importer.markdown_requirements(text)


@pytest.mark.parametrize("sheet", [1, 2])
def test_capability_formulas_are_rejected_even_with_cached_values(tmp_path, sheet):
    name = f"xl/worksheets/sheet{sheet}.xml"
    raw = _members()[name].replace(b"</c>", b"<f>1+1</f><v>2</v></c>", 1)
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="literal values"):
        importer.workbook_tables(workbook)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_declarations_and_entities_are_rejected_in_all_encodings(tmp_path, encoding):
    # A tiny harmless entity proves rejection without constructing an expansion bomb.
    document = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        '<!DOCTYPE worksheet [<!ENTITY owner "harmless">]>'
        + _table_xml([["sentinel"]]).decode().replace("sentinel", "&owner;")
    )
    _, workbook = _inputs(
        tmp_path,
        member_updates={
            "xl/worksheets/sheet1.xml": document.encode(encoding),
        },
    )
    with pytest.raises(ValueError, match="declarations|DTD|entities|encoding"):
        importer.workbook_tables(workbook)


@pytest.mark.parametrize(
    "target", ["../outside.xml", "worksheets/../../outside.xml", "/outside.xml"]
)
def test_unsafe_worksheet_relationships_are_rejected(tmp_path, target):
    name = "xl/_rels/workbook.xml.rels"
    raw = _members()[name].replace(b"worksheets/sheet1.xml", target.encode())
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="Unsafe worksheet relationship"):
        importer.workbook_tables(workbook)


def test_external_worksheet_is_never_followed(tmp_path):
    name = "xl/_rels/workbook.xml.rels"
    raw = _members()[name].replace(
        b'Target="worksheets/sheet1.xml"',
        b'Target="https://example.invalid/never-fetch.xml" TargetMode="External"',
    )
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="Missing local worksheet relationship"):
        importer.workbook_tables(workbook)


def test_duplicate_relationship_ids_are_rejected(tmp_path):
    name = "xl/_rels/workbook.xml.rels"
    raw = _members()[name].replace(
        b"</Relationships>",
        b'<Relationship Id="rId1" Target="worksheets/sheet1.xml" /></Relationships>',
    )
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="[Dd]uplicate|[Aa]mbiguous"):
        importer.workbook_tables(workbook)


def test_duplicate_zip_members_and_worksheet_names_are_rejected(tmp_path):
    with pytest.warns(UserWarning, match="Duplicate name"):
        _, workbook = _inputs(tmp_path, extra_members=[("xl/workbook.xml", b"<unexpected/>")])
    with pytest.raises(ValueError, match="Duplicate workbook member"):
        importer.workbook_tables(workbook)
    name = "xl/workbook.xml"
    _, workbook = _inputs(
        tmp_path,
        member_updates={
            name: _members()[name].replace(b'name="Deliverables"', b'name="Comparison"'),
        },
    )
    with pytest.raises(ValueError, match="Duplicate worksheet name"):
        importer.workbook_tables(workbook)


def test_zip_member_and_decompressed_size_limits_are_enforced(tmp_path, monkeypatch):
    _, workbook = _inputs(tmp_path, extra_members=[(f"unused/{i}", b"") for i in range(201)])
    with pytest.raises(ValueError, match="bounded import size"):
        importer.workbook_tables(workbook)
    _, workbook = _inputs(tmp_path)
    monkeypatch.setattr(importer, "MAX_XML_BYTES", 128)
    with pytest.raises(ValueError, match="bounded import size"):
        importer.workbook_tables(workbook)
    markdown, workbook = _inputs(tmp_path)
    with pytest.raises(ValueError, match="bounded import size"):
        importer.build_catalog(markdown, workbook)


@pytest.mark.parametrize("reference", ["", "A0", "a1", "BM1", "A-1"])
def test_invalid_cell_references_are_rejected(tmp_path, reference):
    name = "xl/worksheets/sheet1.xml"
    raw = _members()[name].replace(b'r="A1"', f'r="{reference}"'.encode(), 1)
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="Invalid.*(reference|column)"):
        importer.workbook_tables(workbook)


def test_duplicate_cell_columns_are_rejected(tmp_path):
    name = "xl/worksheets/sheet1.xml"
    raw = _members()[name].replace(b'r="B1"', b'r="A1"', 1)
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="duplicate workbook column"):
        importer.workbook_tables(workbook)


def test_inline_string_without_contents_is_rejected(tmp_path):
    name = "xl/worksheets/sheet1.xml"
    raw = _members()[name].replace(b"<is><t>ID</t></is>", b"", 1)
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="[Ii]nline"):
        importer.workbook_tables(workbook)


@pytest.mark.parametrize(
    "replacement",
    [
        b"",
        b'Id="rId1" TargetMode="External"',
    ],
)
def test_missing_or_external_duplicate_relationship_ids_are_rejected(tmp_path, replacement):
    name = "xl/_rels/workbook.xml.rels"
    raw = _members()[name].replace(b'Id="rId2"', replacement)
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="[Rr]elationship|[Dd]uplicate|[Aa]mbiguous"):
        importer.workbook_tables(workbook)


def test_nul_padded_xml_is_rejected_before_parsing(tmp_path):
    name = "xl/worksheets/sheet1.xml"
    raw = _members()[name].replace(b"worksheet", b"work\x00sheet", 1)
    _, workbook = _inputs(tmp_path, member_updates={name: raw})
    with pytest.raises(ValueError, match="NUL|encoding|declarations"):
        importer.workbook_tables(workbook)


@pytest.mark.parametrize("index", [-1, 1, 100])
def test_shared_string_indices_are_bounded(tmp_path, index):
    raw = f'<worksheet xmlns="{importer.NS["x"]}"><sheetData><row r="1">'
    raw += f'<c r="A1" t="s"><v>{index}</v></c></row></sheetData></worksheet>'
    _, workbook = _inputs(
        tmp_path,
        member_updates={
            "xl/worksheets/sheet1.xml": raw,
            "xl/sharedStrings.xml": f'<sst xmlns="{importer.NS["x"]}"><si><t>ID</t></si></sst>',
        },
    )
    with pytest.raises(ValueError, match="Invalid shared string index"):
        importer.workbook_tables(workbook)


def test_shared_rich_and_inline_unicode_strings_are_literal(tmp_path):
    raw = f'<worksheet xmlns="{importer.NS["x"]}"><sheetData><row r="1">'
    raw += '<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr">'
    raw += "<is><t>é &amp; Δ</t></is></c></row></sheetData></worksheet>"
    _, workbook = _inputs(
        tmp_path,
        member_updates={
            "xl/worksheets/sheet1.xml": raw,
            "xl/sharedStrings.xml": (
                f'<sst xmlns="{importer.NS["x"]}"><si><r><t>=not</t></r>'
                "<r><t> executed</t></r></si></sst>"
            ),
        },
    )
    assert importer.workbook_tables(workbook)["Comparison"] == [["=not executed", "é & Δ"]]


def test_cli_generation_and_check_do_not_rewrite_sources_or_progress(tmp_path, monkeypatch):
    markdown, workbook = _inputs(tmp_path)
    output, progress = tmp_path / "catalog.json", tmp_path / "daw-progress.json"
    progress.write_text('{"owner_progress":"keep unchanged"}\n')
    original = {path: path.read_bytes() for path in (markdown, workbook, progress)}
    argv = ["import_daw_backlog", str(markdown), str(workbook), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    importer.main()
    generated = output.read_bytes()
    assert json.loads(generated) == importer.build_catalog(markdown, workbook)
    monkeypatch.setattr(sys, "argv", [*argv, "--check"])
    importer.main()
    assert output.read_bytes() == generated
    output.write_bytes(generated + b"\n")
    with pytest.raises(SystemExit) as failure:
        importer.main()
    assert failure.value.code == 2
    assert output.read_bytes() == generated + b"\n"
    for path, content in original.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize("source_index", [0, 1])
def test_cli_cannot_overwrite_either_source(tmp_path, monkeypatch, source_index):
    paths = _inputs(tmp_path)
    original = {path: path.read_bytes() for path in paths}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_daw_backlog",
            *map(str, paths),
            "--output",
            str(paths[source_index]),
        ],
    )
    with pytest.raises(SystemExit) as failure:
        importer.main()
    assert failure.value.code == 2
    for path, content in original.items():
        assert path.read_bytes() == content
