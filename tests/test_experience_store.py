from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.experience_store import (
    AuthorizationCategory,
    ExperienceStore,
    ExperienceStoreError,
    PlanPatchError,
    apply_plan_patch,
    validate_plan_patch,
)


class ExperienceStoreTests(unittest.TestCase):
    def test_store_requires_an_explicit_path(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            ExperienceStore("")

    def test_repair_episode_v1_round_trip(self) -> None:
        with TemporaryDirectory() as temp:
            with ExperienceStore(Path(temp) / "experience.sqlite3") as store:
                episode = store.create_episode(
                    authorization_category=AuthorizationCategory.SYNTHETIC_LOCAL,
                    source_url="https://fixture.invalid/catalog",
                    metadata={"fixture": "catalog-v2"},
                )
                store.append_event(episode, "failure", {"field": "title"})
                proposal = store.add_proposal(
                    episode,
                    {"fields": [{"name": "title", "selector": ".headline"}]},
                    rationale="Local fixture selector changed.",
                )
                store.add_validation(
                    episode,
                    proposal_id=proposal,
                    passed=True,
                    checks={"value": "Northwind notebook"},
                    metrics={"score": 1.0},
                )
                store.add_decision(
                    episode,
                    "accepted",
                    proposal_id=proposal,
                    actor="fixture-reviewer",
                )

                payload = store.get_episode(episode)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["episode"]["status"], "decided")
        self.assertEqual([event["sequence"] for event in payload["events"]], [1, 2, 3, 4])
        self.assertEqual(payload["proposal"]["patch"]["fields"][0]["selector"], ".headline")
        self.assertTrue(payload["validation"]["passed"])
        self.assertEqual(payload["decision"]["outcome"], "accepted")

    def test_capture_policy_uses_sha256_cas_and_withholds_non_synthetic_text(self) -> None:
        secret_text = "private-value-must-not-be-stored"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                local = store.create_episode(authorization_category="synthetic_local")
                local_object = store.put_capture(local, "<h1>Fixture title</h1>", media_type="text/html")
                duplicate = store.put_capture(local, "<h1>Fixture title</h1>", media_type="text/html")
                self.assertEqual(local_object.sha256, duplicate.sha256)
                self.assertEqual(store.get_blob(local_object.sha256), b"<h1>Fixture title</h1>")
                self.assertEqual(local_object.storage_mode, "full")
                object_file = store.cas_root / Path(local_object.object_path)
                self.assertTrue(object_file.is_file())
                self.assertEqual(object_file.read_bytes(), b"<h1>Fixture title</h1>")
                self.assertEqual(object_file.parents[2], store.cas_root / "objects")

                public = store.create_episode(authorization_category="public")
                public_object = store.put_capture(
                    public,
                    f"<main><h1>{secret_text}</h1></main>",
                    media_type="text/html",
                )
                store.append_event(public, "observation", {"html": f"<p>{secret_text}</p>"})
                summary = json.loads(store.get_blob(public_object.sha256))
                self.assertEqual(public_object.storage_mode, "structure_only")
                self.assertEqual(summary["html"]["tags"], {"h1": 1, "main": 1})
                self.assertNotIn(secret_text, json.dumps(summary))
                self.assertNotIn(secret_text, json.dumps(store.get_episode(public)))
                with self.assertRaises(ExperienceStoreError):
                    store.put_capture(
                        public,
                        "must remain structural",
                        authorization_category="synthetic_local",
                    )
                columns = {
                    row[1]
                    for row in store._require_connection().execute("PRAGMA table_info(cas_objects)")
                }
                self.assertIn("object_path", columns)
                self.assertNotIn("body", columns)
                store._require_connection().execute(
                    "UPDATE cas_objects SET object_path = '../../escape' WHERE sha256 = ?",
                    (public_object.sha256,),
                )
                with self.assertRaises(ExperienceStoreError):
                    store.get_blob(public_object.sha256)
                store._require_connection().execute(
                    "UPDATE cas_objects SET object_path = ? WHERE sha256 = ?",
                    (public_object.object_path, public_object.sha256),
                )
            self.assertNotIn(secret_text.encode(), database.read_bytes())
            self.assertNotIn(b"Fixture title", database.read_bytes())
            self.assertTrue(database.with_name(f"{database.name}.cas").is_dir())

    def test_authorized_full_capture_requires_explicit_opt_in_and_still_redacts_sessions(self) -> None:
        marker = "session-never-retained-17b2"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                default_episode = store.create_episode(
                    authorization_category="authorized"
                )
                default_capture = store.put_capture(
                    default_episode,
                    "<h1>Authorized default</h1>",
                    media_type="text/html",
                )
                self.assertEqual(default_capture.storage_mode, "structure_only")

                retained_episode = store.create_episode(
                    authorization_category="authorized",
                    retain_full_content=True,
                )
                retained_capture = store.put_capture(
                    retained_episode,
                    f'<script>{{"session_id":"{marker}"}}</script><h1>Authorized title</h1>',
                    media_type="text/html",
                )
                retained_body = store.get_blob(retained_capture.sha256).decode("utf-8")
                self.assertTrue(retained_episode.retain_full_content)
                self.assertEqual(retained_capture.storage_mode, "redacted_full_opt_in")
                self.assertIn("Authorized title", retained_body)
                self.assertNotIn(marker, retained_body)

                with self.assertRaisesRegex(ValueError, "explicitly authorized"):
                    store.create_episode(
                        authorization_category="public",
                        retain_full_content=True,
                    )
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_credentials_and_browser_state_are_never_persisted(self) -> None:
        marker = "value-never-written-9af7"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    authorization_category="synthetic_local",
                    source_url=f"https://user:{marker}@example.test/?token={marker}",
                    metadata={
                        "cookie": marker,
                        "Authorization": f"Bearer {marker}",
                        "profile": {"path": marker},
                        "localStorage": {"session": marker},
                        "safe": "kept",
                    },
                )
                store.append_event(
                    episode,
                    "diagnostic",
                    {"credentials": marker, "message": f"Authorization: Bearer {marker}"},
                )
                captured = store.put_capture(
                    episode,
                    f"Cookie: session={marker}\nfixture body",
                    media_type="text/plain",
                )
                payload = store.get_episode(episode, include_artifacts=True)
                serialized = json.dumps(payload)
                self.assertNotIn(marker, serialized)
                self.assertEqual(payload["episode"]["metadata"]["safe"], "kept")
                self.assertEqual(captured.storage_mode, "redacted_full")
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_sensitive_key_variants_and_authorized_full_json_are_redacted(self) -> None:
        marker = "variant-secret-never-written-63ae"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    authorization_category="authorized",
                    retain_full_content=True,
                    metadata={
                        "Authorization-Header": f"Bearer {marker}",
                        "browserProfile": marker,
                        "authToken": marker,
                        "safe": "kept",
                    },
                )
                store.append_event(
                    episode,
                    "variant_keys",
                    {"cookieHeader": marker, "userDataDir": marker},
                )
                store.add_decision(
                    episode,
                    "needs_review",
                    metadata={"storageState": marker, "safe": "kept"},
                )
                artifact = store.put_capture(
                    episode,
                    json.dumps(
                        {
                            "authorizationHeader": f"Bearer {marker}",
                            "cookieHeader": marker,
                            "browserProfile": marker,
                            "authToken": marker,
                            "safe": "retained",
                        }
                    ),
                    media_type="application/json",
                )
                payload = store.get_episode(episode)
                retained = store.get_blob(artifact.sha256).decode("utf-8")

                self.assertEqual(payload["episode"]["metadata"]["safe"], "kept")
                self.assertNotIn(marker, json.dumps(payload))
                self.assertNotIn(marker, retained)
                retained_json = json.loads(retained)
                self.assertEqual(retained_json["safe"], "retained")
                self.assertIn("_redacted_fields", retained_json)
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_camel_case_query_and_html_session_apis_are_redacted(self) -> None:
        marker = "html-session-secret-92bf"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    authorization_category="authorized",
                    retain_full_content=True,
                    source_url=(
                        "https://example.test/path?authToken="
                        f"{marker}&clientSecret={marker}&safe=kept"
                    ),
                )
                artifact = store.put_capture(
                    episode,
                    (
                        "<meta name=\"authorization\" content=\"Bearer "
                        f"{marker}\"><script>localStorage.setItem("
                        f"'preferences', '{marker}');sessionStorage['theme']='{marker}'"
                        "</script><main>kept</main>"
                    ),
                    media_type="text/html",
                )
                payload = store.get_episode(episode)
                retained = store.get_blob(artifact.sha256).decode("utf-8")

                serialized = json.dumps(payload)
                self.assertNotIn(marker, serialized)
                self.assertNotIn(marker, retained)
                self.assertIn("safe=kept", payload["episode"]["source_url"])
                self.assertIn("%5BREDACTED%5D", payload["episode"]["source_url"])
                self.assertIn("<main>kept</main>", retained)
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_html_form_values_and_url_userinfo_are_redacted(self) -> None:
        form_marker = "csrf-form-secret-140c"
        url_marker = "userinfo-secret-a910"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    authorization_category="authorized",
                    retain_full_content=True,
                )
                artifact = store.put_capture(
                    episode,
                    (
                        f'<input type="hidden" name="csrfToken" value="{form_marker}">'
                        f'<a href="https://user:{url_marker}@example.test/path">safe</a>'
                    ),
                    media_type="text/html",
                )
                retained = store.get_blob(artifact.sha256).decode("utf-8")
                self.assertNotIn(form_marker, retained)
                self.assertNotIn(url_marker, retained)
                self.assertIn("safe", retained)
            self.assertNotIn(form_marker.encode(), database.read_bytes())
            self.assertNotIn(url_marker.encode(), database.read_bytes())

    def test_csrf_meta_and_script_assignments_are_redacted_from_full_capture(self) -> None:
        marker = "csrf-script-secret-74ac"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    authorization_category="authorized",
                    retain_full_content=True,
                )
                artifact = store.put_capture(
                    episode,
                    (
                        f'<meta content="{marker}" name="csrf-token">'
                        f'<script>window.csrfToken="{marker}";xsrf="{marker}";</script>'
                        "<main>kept</main>"
                    ),
                    media_type="text/html",
                )
                retained = store.get_blob(artifact.sha256).decode("utf-8")

                self.assertNotIn(marker, retained)
                self.assertIn("<main>kept</main>", retained)
                self.assertEqual(artifact.storage_mode, "redacted_full_opt_in")
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_invalid_urls_and_invalid_utf8_text_fail_closed(self) -> None:
        marker = "invalid-input-secret-d1c4"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                malformed = store.create_episode(
                    authorization_category="synthetic_local",
                    source_url=f"https://user:{marker}@[invalid/path",
                )
                invalid_port = store.create_episode(
                    authorization_category="synthetic_local",
                    source_url=f"https://example.test:bad/path?token={marker}",
                )
                artifact = store.put_capture(
                    malformed,
                    b"\xffprefix-" + marker.encode("ascii"),
                    media_type="text/html",
                )

                self.assertEqual(store.get_episode(malformed)["episode"]["source_url"], "")
                self.assertEqual(store.get_episode(invalid_port)["episode"]["source_url"], "")
                self.assertEqual(artifact.storage_mode, "structure_only_invalid_utf8")
                self.assertNotIn(marker, store.get_blob(artifact.sha256).decode("utf-8"))
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_acceptance_requires_latest_passed_replay_validation(self) -> None:
        with TemporaryDirectory() as temp:
            with ExperienceStore(Path(temp) / "experience.sqlite3") as store:
                episode = store.create_episode(authorization_category="synthetic_local")
                proposal = store.add_proposal(
                    episode,
                    {"fields": [{"name": "title", "selector": ".candidate"}]},
                )
                with self.assertRaisesRegex(ExperienceStoreError, "validation to pass"):
                    store.add_decision(episode, "accepted", proposal_id=proposal)

                store.add_validation(episode, proposal_id=proposal, passed=True)
                store.add_validation(episode, proposal_id=proposal, passed=False)
                with self.assertRaisesRegex(ExperienceStoreError, "validation to pass"):
                    store.add_decision(episode, "accepted", proposal_id=proposal)

                payload = store.get_episode(episode)
                self.assertEqual(payload["decisions"], [])

    def test_page_pattern_and_public_decision_metadata_follow_privacy_policy(self) -> None:
        marker = "decision-private-marker-7e91"
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    authorization_category="public",
                    source_url=f"https://alice:{marker}@example.test/items?sessionid={marker}",
                    page_pattern=f"https://alice:{marker}@example.test/items?sessionid={marker}",
                )
                decision = store.add_decision(
                    episode,
                    "needs_review",
                    metadata={"html": f"<main>{marker}</main>", "safe": "kept"},
                )
                payload = store.get_episode(episode)

                self.assertNotIn(marker, json.dumps(payload))
                self.assertNotIn("alice", payload["episode"]["page_pattern"])
                self.assertIn("%5BREDACTED%5D", payload["episode"]["page_pattern"])
                self.assertEqual(decision.metadata["safe"], "kept")
                self.assertIsInstance(decision.metadata["html"], dict)
            self.assertNotIn(marker.encode(), database.read_bytes())

    def test_plan_patch_enforces_scope_allowlist(self) -> None:
        patch = {
            "fields": [{"name": "price", "selector": ".amount", "validation": {"non_empty": {}}}],
            "captures": {"before": True, "after": True},
            "request": {
                "wait_until": "domcontentloaded",
                "wait_for_selector": ".amount",
                "timeout_ms": 5000,
            },
        }
        self.assertEqual(validate_plan_patch(patch), patch)
        base = {"start_url": "https://example.test", "browser": {"headless": True}, "fields": []}
        applied = apply_plan_patch(base, {"fields": patch["fields"]})
        self.assertEqual(applied["start_url"], base["start_url"])
        self.assertEqual(applied["fields"], patch["fields"])

        forbidden = [
            {"start_url": "https://attacker.invalid"},
            {"browser": {"headless": False}},
            {"actions": [{"script": "document.cookie"}]},
            {"fields": [{"name": "title", "selector": "h1", "credentials": "secret"}]},
            {"captures": {"llm": {"endpoint": "https://model.invalid"}}},
        ]
        for candidate in forbidden:
            with self.subTest(candidate=candidate), self.assertRaises(PlanPatchError):
                validate_plan_patch(candidate)

        with self.assertRaises(PlanPatchError):
            validate_plan_patch([{"op": "replace", "path": "/browser/headless", "value": False}])
        allowed_json_patch = [{"op": "replace", "path": "/fields/0/selector", "value": ".new"}]
        self.assertEqual(validate_plan_patch(allowed_json_patch), allowed_json_patch)

        allowed_wait_patch = {
            "request": {"wait_until": "domcontentloaded", "timeout_ms": 5000}
        }
        self.assertEqual(validate_plan_patch(allowed_wait_patch), allowed_wait_patch)
        merged = apply_plan_patch(
            {"request": {"wait_until": "load", "keep": "unchanged"}},
            allowed_wait_patch,
        )
        self.assertEqual(merged["request"]["keep"], "unchanged")
        self.assertEqual(merged["request"]["timeout_ms"], 5000)
        with self.assertRaises(PlanPatchError):
            validate_plan_patch({"request": {"method": "POST"}})
        for ineffective_top_level_wait in (
            {"wait_until": "load"},
            {"wait_for_selector": ".amount"},
            {"timeout_ms": 5000},
        ):
            with self.subTest(patch=ineffective_top_level_wait), self.assertRaises(PlanPatchError):
                validate_plan_patch(ineffective_top_level_wait)
        with self.assertRaises(PlanPatchError):
            validate_plan_patch(
                [{"op": "replace", "path": "/request/method", "value": "POST"}]
            )
        for invalid_timeout in (0, -1, 0.5, 300_001, True):
            with self.subTest(timeout=invalid_timeout), self.assertRaises(PlanPatchError):
                validate_plan_patch({"request": {"timeout_ms": invalid_timeout}})
            with self.subTest(json_timeout=invalid_timeout), self.assertRaises(PlanPatchError):
                validate_plan_patch(
                    [
                        {
                            "op": "replace",
                            "path": "/request/timeout_ms",
                            "value": invalid_timeout,
                        }
                    ]
                )

    def test_validation_passed_requires_a_real_boolean(self) -> None:
        with TemporaryDirectory() as temp:
            with ExperienceStore(Path(temp) / "experience.sqlite3") as store:
                episode = store.create_episode(authorization_category="synthetic_local")
                with self.assertRaisesRegex(ValueError, "true or false"):
                    store.add_validation(episode, passed="false")  # type: ignore[arg-type]
                self.assertEqual(store.get_episode(episode)["validations"], [])

    def test_event_sequence_is_serialized_across_store_connections(self) -> None:
        import concurrent.futures

        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            first = ExperienceStore(database)
            second = ExperienceStore(database)
            try:
                episode = first.create_episode(authorization_category="synthetic_local")

                def append(index: int) -> None:
                    target = first if index % 2 == 0 else second
                    target.append_event(episode.id, "parallel", {"index": index})

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(append, range(40)))

                payload = first.get_episode(episode)
                self.assertEqual(
                    [event["sequence"] for event in payload["events"]],
                    list(range(1, 41)),
                )
            finally:
                first.close()
                second.close()

    def test_proposal_and_audit_event_rollback_together(self) -> None:
        with TemporaryDirectory() as temp:
            with ExperienceStore(Path(temp) / "experience.sqlite3") as store:
                episode = store.create_episode(authorization_category="synthetic_local")
                original = store._append_event_in_transaction

                def fail_event(*_args, **_kwargs):
                    raise RuntimeError("simulated audit write failure")

                store._append_event_in_transaction = fail_event
                try:
                    with self.assertRaisesRegex(RuntimeError, "audit write failure"):
                        store.add_proposal(
                            episode,
                            {"fields": [{"name": "title", "selector": ".new"}]},
                        )
                finally:
                    store._append_event_in_transaction = original

                payload = store.get_episode(episode)
                self.assertEqual(payload["proposals"], [])
                self.assertEqual(payload["events"], [])

    def test_legacy_jsonl_import_is_idempotent_and_cannot_be_promoted(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "repairs.jsonl"
            base = {
                "at": "2026-01-01T00:00:00+00:00",
                "page_pattern": "*example.test/items/*",
                "page_url": "https://example.test/items/1",
                "field": "title",
                "old": ".old",
                "new": ".new",
            }
            legacy.write_text(
                "\n".join(
                    [
                        json.dumps({**base, "ok": True}),
                        json.dumps({**base, "new": ".failed", "ok": False}),
                        json.dumps({**base, "new": ".missing"}),
                        json.dumps({**base, "new": ".string-false", "ok": "false"}),
                        "not-json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with ExperienceStore(root / "experience.sqlite3") as store:
                first = store.import_legacy_jsonl(legacy)
                second = store.import_legacy_jsonl(legacy)
                payload = store.get_episode(first.episode_ids[0])
                proposal_id = payload["proposal"]["id"]
                with self.assertRaises(ExperienceStoreError):
                    store.add_decision(first.episode_ids[0], "accepted", proposal_id=proposal_id)

        self.assertEqual(first.imported, 1)
        self.assertEqual(first.invalid, 4)
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.skipped, 1)
        self.assertEqual(second.invalid, 4)
        self.assertTrue(payload["proposal"]["historical"])
        self.assertEqual(payload["episode"]["status"], "historical")
        self.assertEqual(payload["proposal"]["status"], "historical_candidate")
        self.assertEqual(payload["decision"]["outcome"], "historical_candidate")


if __name__ == "__main__":
    unittest.main()
