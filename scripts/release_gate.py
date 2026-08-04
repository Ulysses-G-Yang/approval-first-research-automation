#!/usr/bin/env python3
"""Validate release versions and create/verify deterministic checksums."""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import tarfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]
SOURCE_VERSION_FILE = ROOT / "research_assistant" / "_version.py"
EXPECTED_DISTRIBUTION = canonicalize_name("generic-crawler-research-assistant")
TAG_PATTERN = re.compile(r"v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)")


def source_version_text() -> str:
    tree = ast.parse(SOURCE_VERSION_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, str):
                        break
                    parsed = Version(value)
                    if str(parsed) != value:
                        raise RuntimeError(
                            f"Source version must use canonical PEP 440 text: {value!r} != {parsed}"
                        )
                    return value
    raise RuntimeError(f"Could not read __version__ from {SOURCE_VERSION_FILE}")


def source_version() -> Version:
    return Version(source_version_text())


def validate_tag(tag: str, version: Version) -> None:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise RuntimeError(f"Release tag must be an immutable vX.Y.Z tag, got: {tag!r}")
    if version.is_devrelease or version.is_prerelease or version.local is not None:
        raise RuntimeError(f"Tagged releases require a final source version, got: {version}")
    if tag != f"v{version}":
        raise RuntimeError(f"Tag/source version mismatch: tag={tag}, source={version}")


def _wheel_metadata(wheel: Path) -> tuple[str, Version, str]:
    parsed_name, parsed_version, _build, _tags = parse_wheel_filename(wheel.name)
    with ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"Expected one wheel METADATA file in {wheel.name}: {metadata_names}")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    metadata_name = canonicalize_name(str(metadata.get("Name", "")))
    metadata_version_text = str(metadata.get("Version", "")).strip()
    metadata_version = Version(metadata_version_text)
    if (
        canonicalize_name(parsed_name) != metadata_name
        or parsed_version != metadata_version
        or str(parsed_version) != metadata_version_text
    ):
        raise RuntimeError(
            f"Wheel filename/METADATA text mismatch: {parsed_name} {parsed_version} vs "
            f"{metadata_name} {metadata_version_text}"
        )
    return metadata_name, metadata_version, metadata_version_text


def _sdist_metadata(sdist: Path) -> tuple[str, Version, str]:
    parsed_name, parsed_version = parse_sdist_filename(sdist.name)
    with tarfile.open(sdist, mode="r:gz") as archive:
        metadata_members = [
            member
            for member in archive.getmembers()
            if member.isfile()
            and PurePosixPath(member.name).name == "PKG-INFO"
            and len(PurePosixPath(member.name).parts) == 2
        ]
        if len(metadata_members) != 1:
            names = [member.name for member in metadata_members]
            raise RuntimeError(f"Expected one sdist PKG-INFO file in {sdist.name}: {names}")
        metadata_file = archive.extractfile(metadata_members[0])
        if metadata_file is None:
            raise RuntimeError(f"Could not read sdist PKG-INFO in {sdist.name}")
        metadata = BytesParser().parsebytes(metadata_file.read())
    metadata_name = canonicalize_name(str(metadata.get("Name", "")))
    metadata_version_text = str(metadata.get("Version", "")).strip()
    metadata_version = Version(metadata_version_text)
    if (
        canonicalize_name(parsed_name) != metadata_name
        or parsed_version != metadata_version
        or str(parsed_version) != metadata_version_text
    ):
        raise RuntimeError(
            f"Sdist filename/PKG-INFO text mismatch: {parsed_name} {parsed_version} vs "
            f"{metadata_name} {metadata_version_text}"
        )
    return metadata_name, metadata_version, metadata_version_text


def validate_dist(
    dist_dir: Path,
    version: Version,
    version_text: str | None = None,
) -> tuple[Path, Path]:
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(f"Expected exactly one wheel and one sdist, got wheels={wheels}, sdists={sdists}")

    wheel_name, wheel_version, wheel_version_text = _wheel_metadata(wheels[0])
    sdist_name, sdist_version, sdist_version_text = _sdist_metadata(sdists[0])
    observed = {
        (wheel_name, wheel_version, wheel_version_text),
        (sdist_name, sdist_version, sdist_version_text),
    }
    expected = {(EXPECTED_DISTRIBUTION, version, version_text or str(version))}
    if observed != expected:
        raise RuntimeError(f"Source/artifact metadata mismatch: expected={expected}, observed={observed}")
    return wheels[0], sdists[0]


def write_checksums(dist_dir: Path, artifacts: tuple[Path, Path]) -> Path:
    checksum_path = dist_dir / "SHA256SUMS.txt"
    lines = []
    for artifact in sorted(artifacts, key=lambda item: item.name):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return checksum_path


def verify_checksums(
    dist_dir: Path,
    expected_artifacts: tuple[Path, Path] | None = None,
) -> None:
    checksum_path = dist_dir / "SHA256SUMS.txt"
    if not checksum_path.is_file():
        raise RuntimeError(f"Missing checksum manifest: {checksum_path}")
    expected_names = (
        {artifact.name for artifact in expected_artifacts}
        if expected_artifacts is not None
        else {
            path.name
            for path in dist_dir.iterdir()
            if path.is_file() and path != checksum_path
        }
    )
    observed_names: set[str] = set()
    lines = checksum_path.read_text(encoding="ascii").splitlines()
    for line in lines:
        digest, separator, name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"Malformed checksum line: {line!r}")
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise RuntimeError(f"Checksum entry must use a plain artifact filename: {name!r}")
        if name in observed_names:
            raise RuntimeError(f"Duplicate checksum entry: {name}")
        observed_names.add(name)
        artifact = dist_dir / name
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Checksum verification failed: {name}")
    if len(lines) != len(expected_names) or observed_names != expected_names:
        raise RuntimeError(
            "Checksum manifest must cover exactly the release artifacts: "
            f"expected={sorted(expected_names)}, observed={sorted(observed_names)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Validate an immutable release tag against the source version.")
    parser.add_argument("--dist", type=Path, help="Validate one wheel and one sdist in this directory.")
    parser.add_argument("--write-checksums", action="store_true")
    parser.add_argument("--verify-checksums", action="store_true")
    args = parser.parse_args(argv)

    version_text = source_version_text()
    version = Version(version_text)
    if args.tag:
        validate_tag(args.tag, version)
    artifacts: tuple[Path, Path] | None = None
    if args.dist:
        artifacts = validate_dist(args.dist, version, version_text)
    if args.write_checksums:
        if args.dist is None or artifacts is None:
            parser.error("--write-checksums requires --dist")
        write_checksums(args.dist, artifacts)
    if args.verify_checksums:
        if args.dist is None:
            parser.error("--verify-checksums requires --dist")
        verify_checksums(args.dist, artifacts)
    print(f"release gate passed: version={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
