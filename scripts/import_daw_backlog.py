#!/usr/bin/env python3
"""Import the owner's static Excel/Markdown comparison without executing cells.

The generated catalog retains historical judgments; implementation progress and
explicit owner policy overrides belong in a separate, reviewed progress file.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ID = re.compile(r"\d{2}-\d{2}")
MAX_XML_BYTES = 16 * 1024 * 1024


def workbook_tables(path):
    """Read literal/cached cells only; no macro, formula or external link execution."""
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 200 or sum(item.file_size for item in members) > MAX_XML_BYTES:
            raise ValueError("Workbook exceeds the bounded import size")
        if len({item.filename for item in members}) != len(members):
            raise ValueError("Duplicate workbook member")

        def xml(name):
            raw = archive.read(name)
            try:
                decoded = raw.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise ValueError("Workbook XML must use UTF-8 encoding") from error
            if "\x00" in decoded or "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
                raise ValueError("Workbook XML declarations are not supported")
            return ET.fromstring(decoded)

        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(node.itertext()) for node in xml("xl/sharedStrings.xml").findall("x:si", NS)
            ]
        relationships = {}
        relationship_ids = set()
        for item in xml("xl/_rels/workbook.xml.rels"):
            identifier = item.get("Id")
            if not identifier or identifier in relationship_ids:
                raise ValueError("Missing or duplicate worksheet relationship ID")
            relationship_ids.add(identifier)
            if item.get("TargetMode") == "External":
                continue
            target = item.get("Target", "")
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if ".." in PurePosixPath(target).parts or not target.startswith("xl/"):
                raise ValueError("Unsafe worksheet relationship")
            relationships[identifier] = target
        result = {}
        for sheet in xml("xl/workbook.xml").findall("x:sheets/x:sheet", NS):
            name = sheet.get("name")
            if name in result:
                raise ValueError("Duplicate worksheet name")
            target = relationships.get(sheet.get(f"{{{REL}}}id"))
            if not target:
                raise ValueError("Missing local worksheet relationship")
            rows = []
            for row in xml(target).findall("x:sheetData/x:row", NS):
                values = {}
                for cell in row.findall("x:c", NS):
                    reference = cell.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,3})[1-9]\d*", reference)
                    if not match:
                        raise ValueError("Invalid workbook cell reference")
                    column = 0
                    for character in match[1]:
                        column = column * 26 + ord(character) - ord("A") + 1
                    if column > 64 or column in values:
                        raise ValueError("Invalid or duplicate workbook column")
                    value = cell.findtext("x:v", default="", namespaces=NS)
                    if cell.get("t") == "inlineStr":
                        inline = cell.find("x:is", NS)
                        if inline is None:
                            raise ValueError("Missing inline workbook string")
                        value = "".join(inline.itertext())
                    elif cell.get("t") == "s":
                        index = int(value)
                        if not 0 <= index < len(strings):
                            raise ValueError("Invalid shared string index")
                        value = strings[index]
                    # Formulas are never evaluated. Capability tables must be literal.
                    if cell.find("x:f", NS) is not None and name in ("Comparison", "Deliverables"):
                        raise ValueError("Capability tables must contain literal values")
                    values[column] = value
                rows.append(
                    [values.get(index, "") for index in range(1, max(values, default=0) + 1)]
                )
                if len(rows) > 10000:
                    raise ValueError("Too many workbook rows")
            result[name] = rows
        return result


def table_records(rows):
    header = None
    result = {}
    for row in rows:
        if row and row[0] == "ID":
            if header is not None or len(set(row)) != len(row):
                raise ValueError("Ambiguous table header")
            header = row
        elif row and ID.fullmatch(row[0]):
            if header is None or row[0] in result or len(row) > len(header):
                raise ValueError("Invalid or duplicate capability row")
            result[row[0]] = dict(zip(header, row + [""] * (len(header) - len(row)), strict=True))
    if not header or not result:
        raise ValueError("Missing capability table")
    return result


def markdown_requirements(text):
    pattern = re.compile(
        r"^- \[ \] \*\*(\d{2}-\d{2}) — (.+?)\*\* · (.+?) · (P[0-3])\n"
        r"  - Current source: (.+)\n  - Done when: (.+)\n"
        r"  - Benchmark examples: (.+)\n"
        r"  - \[Relevant source architecture\]\((https://[^\s)]+)\)",
        re.M,
    )
    result = {}
    for item in pattern.finditer(text):
        identifier, title, status, priority, limitation, done_when, benchmarks, evidence = (
            item.groups()
        )
        if identifier in result:
            raise ValueError("Duplicate Markdown capability")
        result[identifier] = dict(
            title=title,
            baseline_status=status,
            priority=priority,
            baseline_limitation=limitation,
            acceptance=done_when,
            benchmark_examples=benchmarks,
            source_evidence=evidence,
        )
    if len(result) != len(re.findall(r"^- \[ \] \*\*", text, re.M)):
        raise ValueError("Some Markdown requirements could not be read")
    return result


def build_catalog(markdown_path, workbook_path):
    if markdown_path.stat().st_size > MAX_XML_BYTES or workbook_path.stat().st_size > MAX_XML_BYTES:
        raise ValueError("Source documents exceed the bounded import size")
    raw = markdown_path.read_bytes()
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("Markdown exceeds the bounded import size")
    text = raw.decode("utf-8")
    requirements = markdown_requirements(text)
    tables = workbook_tables(workbook_path)
    comparison = table_records(tables["Comparison"])
    deliverables = table_records(tables["Deliverables"])
    if set(requirements) != set(deliverables):
        raise ValueError("Markdown and workbook deliverable IDs differ")
    if set(deliverables) - set(comparison):
        raise ValueError("Deliverables absent from comparison")
    for identifier, requirement in requirements.items():
        row = deliverables[identifier]
        for key, column in (
            ("title", "Deliverable"),
            ("baseline_status", "Source status"),
            ("priority", "Priority"),
            ("baseline_limitation", "Current limitation"),
            ("acceptance", "Done when"),
        ):
            if requirement[key] != row[column]:
                raise ValueError(f"Source documents disagree for {identifier}: {column}")
    capabilities = []
    for identifier, row in comparison.items():
        item = dict(
            id=identifier,
            area=row["Area"],
            title=row["Capability / deliverable"],
            baseline_status=row["Anharmonic"],
            priority=row["Priority"],
            baseline_limitation=row["Current implementation"],
            acceptance=None,
            source_evidence=row["Code evidence"],
            work_package=identifier in deliverables,
        )
        if identifier in requirements:
            requirement = requirements[identifier]
            for key in ("title", "baseline_status", "priority"):
                if item[key] != requirement[key]:
                    raise ValueError(f"Workbook sheets disagree for {identifier}: {key}")
            item.update(requirement)
        item["manufacturer_cells_as_supplied"] = {
            key: value
            for key, value in row.items()
            if key
            not in {
                "ID",
                "Area",
                "Capability / deliverable",
                "Anharmonic",
                "Priority",
                "Code evidence",
                "Current implementation",
            }
        }
        capabilities.append(item)
    snapshot = re.search(r"Source snapshot: .*?/tree/([0-9a-f]{40})", text)
    if not snapshot:
        raise ValueError("Missing source snapshot provenance")
    return dict(
        schema_version=1,
        provenance={
            "markdown": {"name": markdown_path.name, "sha256": hashlib.sha256(raw).hexdigest()},
            "workbook": {
                "name": workbook_path.name,
                "sha256": hashlib.sha256(workbook_path.read_bytes()).hexdigest(),
            },
            "reviewed_source_commit": snapshot[1],
            "review_type": "Owner-supplied static audit; historical claims, not runtime acceptance or independent manufacturer verification",
        },
        counts={
            "capabilities": len(capabilities),
            "work_packages": len(deliverables),
            "baseline_status": dict(Counter(item["baseline_status"] for item in capabilities)),
        },
        capabilities=capabilities,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--check", action="store_true", help="Compare without changing the generated catalog"
    )
    args = parser.parse_args()
    if args.output.resolve() in (args.markdown.resolve(), args.workbook.resolve()):
        parser.error("The generated catalog cannot replace a source document")
    catalog = build_catalog(args.markdown, args.workbook)
    output = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if args.output.read_text(encoding="utf-8") != output:
            parser.error("Generated catalog differs from source documents")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(json.dumps(catalog["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
