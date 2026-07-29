from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from project_glacier_orchestrator.checkpoint import ResumeStore, WorkspacePaths
from project_glacier_orchestrator.errors import OrchestratorError
from project_glacier_orchestrator.protocol import WORKER_PROMPT_VERSION
from project_glacier_orchestrator.workflow import (
    REQUIRED_COORDINATION_KEYS,
    Coordinator,
    validate_coordination_state,
)
from scripts.initialize_workspace import (
    REPOSITORY_ROOT,
    RESUME_NAME,
    RUNTIME_TEMPLATE_NAMES,
    InitializationError,
    _secure_read_at,
    initialize_workspace,
)


class InitializeWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name).resolve() / "workspace")
        self.root.mkdir()
        shutil.copyfile(
            REPOSITORY_ROOT / "project_glacier_offline_root_worker_prompt.md",
            self.root / "project_glacier_offline_root_worker_prompt.md",
        )

    def initialize(self) -> tuple[Path, ...]:
        return initialize_workspace(
            self.root,
            generated_at=datetime(2026, 7, 29, 12, 34, 56, tzinfo=UTC),
        )

    def test_initialization_creates_private_neutral_artifacts_and_valid_resume(self) -> None:
        prompt = self.root / "project_glacier_offline_root_worker_prompt.md"
        prompt_before = prompt.read_bytes()
        created = self.initialize()
        expected_names = {*RUNTIME_TEMPLATE_NAMES, RESUME_NAME}
        self.assertEqual(expected_names, {path.name for path in created})
        self.assertEqual(
            tuple(
                self.root / name
                for name in (*RUNTIME_TEMPLATE_NAMES, RESUME_NAME)
            ),
            created,
        )
        for path in created:
            self.assertTrue(path.is_file())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        for name in RUNTIME_TEMPLATE_NAMES:
            self.assertEqual(
                (REPOSITORY_ROOT / "templates/runtime" / name).read_bytes(),
                (self.root / name).read_bytes(),
            )
        self.assertEqual(prompt_before, prompt.read_bytes())

        store = ResumeStore(
            WorkspacePaths.from_root(self.root),
            required_keys=REQUIRED_COORDINATION_KEYS,
            state_validator=validate_coordination_state,
        )
        document = store.read()
        digests = store.artifact_digests()
        self.assertEqual("SINGLE_ROOT", document.values["orchestration_mode"])
        self.assertEqual("true", document.values["checkpoint_reconciliation_required"])
        self.assertEqual(
            "checkpoint-reconciliation-required",
            document.values["global_mutation_lock"],
        )
        self.assertEqual(
            "2026-07-29T12:34:56Z",
            document.values["checkpoint_generated_at"],
        )
        self.assertEqual("none", document.values["active_case_id"])
        self.assertEqual("none", document.values["active_case_state"])
        self.assertEqual("UNREGISTERED", document.values["offline_worker_status"])
        self.assertEqual("none", document.values["offline_worker_thread_id"])
        self.assertEqual("unknown", document.values["submission_enabled"])
        self.assertEqual("unknown", document.values["submission_count"])
        self.assertEqual("unknown", document.values["submission_limit"])
        self.assertEqual("unknown", document.values["remaining_submission_budget"])
        self.assertEqual("unknown", document.values["last_delivery_status"])
        self.assertEqual("none", document.values["prepared_payload_hash"])
        self.assertEqual("none", document.values["submission_queue"])
        self.assertEqual(WORKER_PROMPT_VERSION, document.values["offline_worker_prompt_version"])
        self.assertEqual(digests.rules, document.values["rules_digest"])
        self.assertEqual(digests.ledger, document.values["ledger_digest"])
        self.assertEqual(digests.worker_prompt, document.values["offline_worker_prompt_digest"])
        self.assertEqual(document.computed_hash(), document.state_hash)

        coordinator = Coordinator(self.root)
        snapshot_before = {
            path.name: (path.read_bytes(), path.stat().st_ino)
            for path in created
        }
        with self.assertRaises(OrchestratorError) as caught:
            coordinator.activate_pilot(
                expected_version=document.version,
                expected_hash=document.state_hash,
                user_authorized_offline_worker=True,
                commit=False,
            )
        self.assertEqual("CHECKPOINT_RECONCILIATION_REQUIRED", caught.exception.code)
        self.assertEqual(
            snapshot_before,
            {
                path.name: (path.read_bytes(), path.stat().st_ino)
                for path in created
            },
        )
        self.assertFalse((self.root / ".project-glacier-orchestrator").exists())

    def test_templates_contain_no_live_ids_or_tenancy_identifiers(self) -> None:
        self.initialize()
        combined = b"\n".join(
            (self.root / name).read_bytes() for name in (*RUNTIME_TEMPLATE_NAMES, RESUME_NAME)
        ).decode("utf-8")
        self.assertIsNone(re.search(r"\bocid1\.", combined, flags=re.IGNORECASE))
        self.assertIsNone(re.search(r"\bcase_[A-Za-z0-9_-]+", combined))
        self.assertIsNone(re.search(r"\bGL-[0-9]{8}-[0-9]+\b", combined))

    def test_existing_destination_aborts_before_any_new_artifact_is_created(self) -> None:
        existing = self.root / "CURRENT_CHECKPOINT.md"
        existing.write_text("preserve me", encoding="utf-8")
        original = existing.read_bytes()
        with self.assertRaises(InitializationError):
            self.initialize()
        self.assertEqual(original, existing.read_bytes())
        for name in (*RUNTIME_TEMPLATE_NAMES[1:], RESUME_NAME):
            self.assertFalse((self.root / name).exists())

    def test_symlink_destination_is_rejected_without_touching_target(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("untouched", encoding="utf-8")
        (self.root / "CURRENT_CHECKPOINT.md").symlink_to(outside)
        with self.assertRaises(InitializationError):
            self.initialize()
        self.assertEqual("untouched", outside.read_text(encoding="utf-8"))
        self.assertFalse((self.root / RESUME_NAME).exists())

    def test_staging_fsync_failure_leaves_no_outputs_or_temporary_files(self) -> None:
        with (
            patch(
                "scripts.initialize_workspace.os.fsync",
                side_effect=OSError("simulated fsync failure"),
            ),
            self.assertRaises(InitializationError),
        ):
            self.initialize()

        self.assertTrue(
            all(
                not (self.root / name).exists()
                for name in (*RUNTIME_TEMPLATE_NAMES, RESUME_NAME)
            )
        )
        self.assertEqual(
            [],
            list(self.root.glob(".project-glacier-init-*")),
        )

    def test_keyboard_interrupt_during_publication_rolls_back_complete_set(self) -> None:
        real_link = os.link
        link_count = 0

        def interrupt_second_link(*args, **kwargs):
            nonlocal link_count
            link_count += 1
            if link_count == 2:
                raise KeyboardInterrupt
            return real_link(*args, **kwargs)

        with (
            patch(
                "scripts.initialize_workspace.os.link",
                side_effect=interrupt_second_link,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.initialize()

        self.assertTrue(
            all(
                not (self.root / name).exists()
                for name in (*RUNTIME_TEMPLATE_NAMES, RESUME_NAME)
            )
        )
        self.assertEqual([], list(self.root.glob(".project-glacier-init-*")))

    def test_staged_path_substitution_is_detected_without_deleting_substitute(self) -> None:
        real_link = os.link
        substituted = False

        def substitute_before_link(source, destination, **kwargs):
            nonlocal substituted
            if not substituted:
                substituted = True
                source_dir = kwargs["src_dir_fd"]
                os.unlink(source, dir_fd=source_dir)
                descriptor = os.open(
                    source,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=source_dir,
                )
                try:
                    os.write(descriptor, b"substituted\n")
                finally:
                    os.close(descriptor)
            return real_link(source, destination, **kwargs)

        with (
            patch(
                "scripts.initialize_workspace.os.link",
                side_effect=substitute_before_link,
            ),
            self.assertRaises(InitializationError),
        ):
            self.initialize()

        substituted_destination = self.root / RUNTIME_TEMPLATE_NAMES[0]
        self.assertEqual(
            b"substituted\n",
            substituted_destination.read_bytes(),
        )
        self.assertTrue(
            all(
                not (self.root / name).exists()
                for name in (*RUNTIME_TEMPLATE_NAMES[1:], RESUME_NAME)
            )
        )
        self.assertEqual([], list(self.root.glob(".project-glacier-init-*")))

    def test_group_or_other_writable_workspace_is_rejected(self) -> None:
        self.root.chmod(0o777)

        with self.assertRaisesRegex(
            InitializationError,
            "must not be writable by group or other users",
        ):
            self.initialize()

        self.assertFalse((self.root / RESUME_NAME).exists())

    def test_workspace_path_replacement_is_detected_before_publication(self) -> None:
        moved = self.root.with_name(self.root.name + "-moved")

        def replace_workspace(directory_descriptor: int, name: str) -> bytes:
            content = _secure_read_at(directory_descriptor, name)
            self.root.rename(moved)
            self.root.mkdir()
            return content

        with (
            patch(
                "scripts.initialize_workspace._secure_read_at",
                side_effect=replace_workspace,
            ),
            self.assertRaisesRegex(InitializationError, "identity changed"),
        ):
            self.initialize()

        for workspace in (self.root, moved):
            self.assertTrue(
                all(
                    not (workspace / name).exists()
                    for name in (*RUNTIME_TEMPLATE_NAMES, RESUME_NAME)
                )
            )

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "requires POSIX no-follow support")
    def test_symlink_workspace_path_is_rejected(self) -> None:
        linked = self.root.parent / "linked-workspace"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(InitializationError):
            initialize_workspace(linked)
        self.assertFalse((self.root / RESUME_NAME).exists())


if __name__ == "__main__":
    unittest.main()
