from __future__ import annotations

import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.funasr_phase0 import lib_license_audit as audit


def fake_dist(name: str, version: str, *, expression: str = "", license_field: str = "",
              classifiers: list[str] | None = None):
    metadata = Message()
    metadata["Name"] = name
    if expression:
        metadata["License-Expression"] = expression
    if license_field:
        metadata["License"] = license_field
    for classifier in classifiers or []:
        metadata["Classifier"] = classifier
    return SimpleNamespace(name=name, version=version, metadata=metadata)


class TestPackageEvidence(unittest.TestCase):
    def test_pep639_has_priority(self):
        pkg = audit._package_info(fake_dist(
            "demo", "1", expression="MIT OR Apache-2.0",
            license_field="GNU GENERAL PUBLIC LICENSE appears in a bundled notice",
            classifiers=["License :: OSI Approved :: BSD License"],
        ))
        self.assertEqual(pkg.selected_source, "license-expression")
        self.assertEqual(pkg.tier, 1)
        self.assertEqual(pkg.constituents, ["MIT", "Apache-2.0"])

    def test_long_numpy_scipy_style_notice_does_not_override_classifier(self):
        notice = "BSD license for the project\n" + ("bundled GCC runtime GPL exception\n" * 30)
        pkg = audit._package_info(fake_dist(
            "numpy", "1.26.4", license_field=notice,
            classifiers=["License :: OSI Approved :: BSD License"],
        ))
        self.assertEqual(pkg.selected_source, "classifier")
        self.assertEqual(pkg.selected_license, "BSD-3-Clause")
        self.assertEqual(pkg.tier, 1)
        self.assertIsNotNone(pkg.notice_sha256)

    def test_common_classifier_phrases_are_recognized(self):
        for declaration, expected in (
            ("License :: OSI Approved :: Apache Software License", "Apache-2.0"),
            ("MIT License", "MIT"),
            ("Mozilla Public License 2.0", "MPL-2.0"),
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(expected, audit.classify_license(declaration)[1])


class TestModelEvidence(unittest.TestCase):
    def test_model_card_frontmatter_is_actual_evidence_and_revision_is_bound(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = root / "iic" / "SenseVoiceSmall"
            model.mkdir(parents=True)
            (model / "README.md").write_text("---\nlicense: Apache-2.0\n---\n", encoding="utf-8")
            (model / "model.pt").write_bytes(b"weights")
            result = audit.scan_models(root, [{
                "model_id": "iic/SenseVoiceSmall", "revision": "abc123",
                "source_url": "https://example.invalid", "expected_license": "Apache-2.0",
            }])[0]
            self.assertEqual(result.status, "VERIFIED")
            self.assertEqual(result.revision, "abc123")
            self.assertEqual(result.declared_license, "Apache-2.0")
            self.assertIn("model.pt", result.found_files_sha256)

    def test_expected_license_alone_never_verifies_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = root / "iic" / "SenseVoiceSmall"
            model.mkdir(parents=True)
            (model / "model.pt").write_bytes(b"weights")
            result = audit.scan_models(root, [{
                "model_id": "iic/SenseVoiceSmall", "revision": "abc123",
                "expected_license": "Apache-2.0",
            }])[0]
            self.assertEqual(result.status, "NO_LICENSE_EVIDENCE")
            self.assertEqual(result.found_license_tier, 0)


class TestApprovals(unittest.TestCase):
    def _pkg(self) -> audit.PkgInfo:
        return audit._package_info(fake_dist("certifi", "2026.7.22", expression="MPL-2.0"))

    def test_exact_approval_is_accepted(self):
        pkg = self._pkg()
        approval = {("package", pkg.name, pkg.version): {
            "evidence_sha256": pkg.evidence_sha256, "reason": "reviewed",
        }}
        audit._apply_approvals([pkg], [], approval)
        self.assertTrue(pkg.approved)
        self.assertFalse(audit._package_blocked(pkg))

    def test_wrong_evidence_digest_is_rejected(self):
        pkg = self._pkg()
        approval = {("package", pkg.name, pkg.version): {
            "evidence_sha256": "0" * 64, "reason": "stale",
        }}
        audit._apply_approvals([pkg], [], approval)
        self.assertFalse(pkg.approved)
        self.assertTrue(audit._package_blocked(pkg))

    def test_stale_config_and_wildcard_approval_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "approvals.json"
            base = {
                "schema_version": audit.APPROVAL_SCHEMA_VERSION,
                "config_sha256": "a" * 64,
                "approvals": [{
                    "artifact_type": "package", "name": "*", "version_or_revision": "1",
                    "evidence_sha256": "b" * 64, "approved_by": "owner",
                    "approved_at": "2026-07-31T00:00:00+08:00", "reason": "reviewed",
                }],
            }
            path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "config_sha256"):
                audit._load_approvals(path, "c" * 64)
            with self.assertRaisesRegex(ValueError, "wildcards"):
                audit._load_approvals(path, "a" * 64)

    def test_naive_approval_time_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "approvals.json"
            path.write_text(json.dumps({
                "schema_version": audit.APPROVAL_SCHEMA_VERSION,
                "config_sha256": "a" * 64,
                "approvals": [{
                    "artifact_type": "package", "name": "certifi",
                    "version_or_revision": "1", "evidence_sha256": "b" * 64,
                    "approved_by": "owner", "approved_at": "2026-07-31T21:00:00",
                    "reason": "reviewed",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "UTC offset"):
                audit._load_approvals(path, "a" * 64)


if __name__ == "__main__":
    unittest.main()
