from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.version import Version

from scripts.release_gate import (
    source_version,
    validate_dist,
    validate_tag,
    verify_checksums,
    write_checksums,
)


class ReleaseGateTests(unittest.TestCase):
    distribution = "generic-crawler-research-assistant"

    def _write_wheel(
        self,
        root: Path,
        version: Version,
        *,
        metadata_name: str | None = None,
        metadata_version: str | None = None,
    ) -> Path:
        wheel = root / f"generic_crawler_research_assistant-{version}-py3-none-any.whl"
        dist_info = f"generic_crawler_research_assistant-{version}.dist-info"
        metadata = (
            "Metadata-Version: 2.1\n"
            f"Name: {metadata_name or self.distribution}\n"
            f"Version: {metadata_version or version}\n\n"
        )
        with ZipFile(wheel, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(f"{dist_info}/METADATA", metadata)
        return wheel

    def _write_sdist(
        self,
        root: Path,
        version: Version,
        *,
        metadata_name: str | None = None,
        metadata_version: str | None = None,
    ) -> Path:
        sdist = root / f"generic_crawler_research_assistant-{version}.tar.gz"
        metadata = (
            "Metadata-Version: 2.1\n"
            f"Name: {metadata_name or self.distribution}\n"
            f"Version: {metadata_version or version}\n\n"
        ).encode("utf-8")
        member = tarfile.TarInfo(
            f"generic_crawler_research_assistant-{version}/PKG-INFO"
        )
        member.size = len(metadata)
        with tarfile.open(sdist, mode="w:gz") as archive:
            archive.addfile(member, io.BytesIO(metadata))
            nested = tarfile.TarInfo(
                f"generic_crawler_research_assistant-{version}/"
                "generic_crawler_research_assistant.egg-info/PKG-INFO"
            )
            nested.size = len(metadata)
            archive.addfile(nested, io.BytesIO(metadata))
        return sdist

    def test_source_version_is_pep440_and_tag_gate_matches_release_state(self) -> None:
        version = source_version()
        self.assertIsInstance(version, Version)
        if version.is_devrelease:
            with self.assertRaisesRegex(RuntimeError, "final source version"):
                validate_tag("v2.1.0", version)
        else:
            validate_tag(f"v{version}", version)
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                validate_tag("v99.99.99", version)
        with self.assertRaisesRegex(RuntimeError, "vX.Y.Z"):
            validate_tag("not-a-release-tag", version)
        if not version.is_devrelease:
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                validate_tag("v02.01.000", version)

    def test_checksum_manifest_is_deterministic_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "example.whl"
            sdist = root / "example.tar.gz"
            wheel.write_bytes(b"wheel")
            sdist.write_bytes(b"sdist")
            first = write_checksums(root, (wheel, sdist)).read_bytes()
            second = write_checksums(root, (sdist, wheel)).read_bytes()
            self.assertEqual(first, second)
            verify_checksums(root, (wheel, sdist))
            wheel.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "Checksum verification failed"):
                verify_checksums(root, (wheel, sdist))

            wheel.write_bytes(b"wheel")
            (root / "SHA256SUMS.txt").write_text("", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "exactly the release artifacts"):
                verify_checksums(root, (wheel, sdist))

    def test_validate_dist_checks_wheel_and_sdist_embedded_metadata(self) -> None:
        version = source_version()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = self._write_wheel(root, version)
            sdist = self._write_sdist(root, version)
            self.assertEqual(validate_dist(root, version), (wheel, sdist))

    def test_validate_dist_rejects_wheel_metadata_mismatch(self) -> None:
        version = source_version()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_wheel(root, version, metadata_version="99.0.0")
            self._write_sdist(root, version)
            with self.assertRaisesRegex(RuntimeError, "Wheel filename/METADATA text mismatch"):
                validate_dist(root, version)

    def test_validate_dist_rejects_semantically_equal_wheel_version_text(self) -> None:
        version = source_version()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_wheel(root, version, metadata_version=f"v{version}")
            self._write_sdist(root, version)
            with self.assertRaisesRegex(RuntimeError, "Wheel filename/METADATA text mismatch"):
                validate_dist(root, version)

    def test_validate_dist_rejects_sdist_metadata_mismatch(self) -> None:
        version = source_version()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_wheel(root, version)
            self._write_sdist(root, version, metadata_name="different-project")
            with self.assertRaisesRegex(RuntimeError, "Sdist filename/PKG-INFO text mismatch"):
                validate_dist(root, version)

    def test_validate_dist_rejects_semantically_equal_sdist_version_text(self) -> None:
        version = source_version()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_wheel(root, version)
            self._write_sdist(root, version, metadata_version=f"v{version}")
            with self.assertRaisesRegex(RuntimeError, "Sdist filename/PKG-INFO text mismatch"):
                validate_dist(root, version)

    def test_validate_dist_rejects_extra_distribution_artifacts(self) -> None:
        version = source_version()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_wheel(root, version)
            self._write_sdist(root, version)
            (root / "extra-1.0.0-py3-none-any.whl").write_bytes(b"extra")
            with self.assertRaisesRegex(RuntimeError, "exactly one wheel and one sdist"):
                validate_dist(root, version)


if __name__ == "__main__":
    unittest.main()
