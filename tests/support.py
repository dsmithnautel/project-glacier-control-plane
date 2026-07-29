"""Isolated fixtures and valid protocol packets used by the unittest suite."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from project_glacier_orchestrator.canonical import sha256_hex
from project_glacier_orchestrator.checkpoint import ResumeDocument
from project_glacier_orchestrator.protocol import OFFICIAL_CRITERIA, ROLE, WORKER_PROMPT_VERSION

SOURCE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ARTIFACTS = (
    ("templates/runtime/glacier_rules_dossier.md", "glacier_rules_dossier.md"),
    ("templates/runtime/glacier_experiment_ledger.md", "glacier_experiment_ledger.md"),
    (
        "project_glacier_offline_root_worker_prompt.md",
        "project_glacier_offline_root_worker_prompt.md",
    ),
)


class DeterministicIds:
    """Generate valid, predictable IDs without patching randomness globally."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        count = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = count
        return f"{prefix}-{count}"


def make_workspace(parent: Path, *, mode: str = "SINGLE_ROOT") -> Path:
    """Copy source artifact bytes and generate a self-consistent v2 resume file."""

    root = parent / "workspace"
    root.mkdir()
    for source_name, destination_name in SOURCE_ARTIFACTS:
        (root / destination_name).write_bytes((SOURCE_ROOT / source_name).read_bytes())
    (root / ".codex").mkdir()
    (root / ".codex" / "config.toml").write_text(
        "[agents]\n"
        "max_threads = 30\n"
        "\n"
        "[mcp_servers.project_glacier]\n"
        "required = true\n",
        encoding="utf-8",
    )

    rules_digest = sha256_hex((root / "glacier_rules_dossier.md").read_bytes())
    ledger_digest = sha256_hex((root / "glacier_experiment_ledger.md").read_bytes())
    prompt_digest = sha256_hex(
        (root / "project_glacier_offline_root_worker_prompt.md").read_bytes()
    )
    values = OrderedDict(
        [
            ("orchestration_mode", mode),
            ("multi_root_protocol_version", "2"),
            ("canonical_state_version", "1"),
            ("canonical_state_hash", "0" * 64),
            ("checkpoint_generated_at", "2026-07-12T00:00:00Z"),
            ("checkpoint_reconciliation_required", "false"),
            ("global_mutation_lock", "none"),
            ("active_case_id", "none"),
            ("active_case_state", "none"),
            ("prepared_payload_hash", "none"),
            ("submission_queue", "none"),
            ("last_delivery_status", "ACKNOWLEDGED"),
            ("multi_root_leadership_epoch", "0"),
            ("multi_root_replay_window_size", "256"),
            ("multi_root_processed_message_ids", "[]"),
            ("multi_root_processed_result_ids", "[]"),
            ("rules_digest", rules_digest),
            ("ledger_digest", ledger_digest),
            ("offline_worker_thread_id", "none"),
            ("offline_worker_host_id", "none"),
            ("offline_worker_prompt_version", WORKER_PROMPT_VERSION),
            ("offline_worker_prompt_digest", prompt_digest),
            ("offline_worker_bootstrap_message_id", "none"),
            ("offline_worker_attested_prompt_version", "none"),
            ("offline_worker_attested_prompt_digest", "none"),
            ("offline_worker_pending_message_id", "none"),
            ("offline_worker_expected_response_type", "NONE"),
            ("offline_worker_status", "UNREGISTERED"),
            ("offline_worker_last_ack_version", "none"),
            ("next_worker_assignment_sequence", "1"),
            ("active_worker_assignment_id", "none"),
            ("active_worker_assignment_message_id", "none"),
            ("active_worker_assignment_base_version", "none"),
            ("active_worker_assignment_base_hash", "none"),
            ("active_worker_assignment_rules_digest", "none"),
            ("active_worker_assignment_ledger_digest", "none"),
            ("active_worker_assignment_live_state_digest", "none"),
            ("pending_worker_result_id", "none"),
            ("pending_worker_result_digest", "none"),
            ("pending_worker_result_assignment_id", "none"),
            ("last_accepted_worker_result_id", "none"),
            ("last_worker_result_disposition", "none"),
            ("coordination_outbox_message_id", "none"),
            ("coordination_outbox_message_type", "none"),
            ("coordination_outbox_payload_digest", "none"),
            ("coordination_outbox_delivery_status", "NONE"),
            ("worker_coordination_blocker", "none"),
        ]
    )
    document = ResumeDocument(values)
    document.values["canonical_state_hash"] = document.computed_hash()
    (root / "PROJECT_GLACIER_RESUME.txt").write_text(document.render(), encoding="utf-8")
    return root


def valid_assignment_spec() -> dict[str, Any]:
    return {
        "objective": "Develop one offline research hypothesis from the supplied public facts.",
        "allowed_public_context": ["The synthetic service has a documented policy boundary."],
        "coverage_cells": ["policy-boundary"],
        "constraints": ["Reason offline only", "Do not claim live verification"],
        "exclusion_list": ["Previously evaluated family alpha"],
        "stop_conditions": ["Stop after one well-falsified candidate"],
    }


def valid_research_item() -> dict[str, Any]:
    return {
        "family_id": "family-beta",
        "hypothesis_id": "hypothesis-beta-1",
        "policy_category_subcategory": "authorization-boundary",
        "causal_mechanism": "A synthetic boundary mismatch could change an allow decision.",
        "material_novelty_delta": "Separates decision context from request shape.",
        "synthetic_oci_scenario": "A fictional tenant sends two equivalent benign requests.",
        "smallest_controlled_experiment": (
            "Compare one baseline and one isolated policy-context variant."
        ),
        "expected_observations": {
            "allow": "Both benign requests retain the documented allow behavior.",
            "block": "Only the policy-context variant receives the documented block behavior.",
        },
        "benign_or_negative_control": "Repeat the baseline without changing policy context.",
        "scope_and_safety_analysis": "Uses synthetic identifiers and no live systems.",
        "dependencies_or_required_discovery": ["Confirm the public policy-category vocabulary."],
        "estimated_value_across_five_official_criteria": [
            {"criterion": criterion, "assessment": f"Moderate value for {criterion.lower()}."}
            for criterion in OFFICIAL_CRITERIA
        ],
        "falsifier": "No decision difference after controlling request shape.",
        "recommended_next_action": "RESEARCH",
    }


def valid_result_packet(
    assignment: dict[str, Any],
    *,
    thread_id: str = "worker-task-1",
    message_id: str = "result-1",
) -> dict[str, Any]:
    return {
        "type": "RESULT_PACKET",
        "protocol_version": 2,
        "message_id": message_id,
        "assignment_id": assignment["assignment_id"],
        "worker_task_id": thread_id,
        "role": ROLE,
        "worker_prompt_version": assignment["worker_prompt_version"],
        "worker_prompt_digest": assignment["worker_prompt_digest"],
        "leadership_epoch": assignment["leadership_epoch"],
        "base_state_version": assignment["base_state_version"],
        "base_state_hash": assignment["base_state_hash"],
        "rules_digest": assignment["rules_digest"],
        "ledger_digest": assignment["ledger_digest"],
        "status": "COMPLETE",
        "result_digest": None,
        "results": [valid_research_item()],
        "assumptions": ["Packet facts are current for this offline snapshot."],
        "evidence_basis": [
            {"claim": "The scenario is synthetic.", "basis": "PACKET_FACT"},
            {"claim": "The boundary may be informative.", "basis": "INFERENCE"},
        ],
        "duplicate_risk": "Low after comparison with the supplied exclusion list.",
        "recommended_next_action": "RESEARCH",
        "child_assignments": ["Review novelty", "Review falsifiability"],
        "children_running": 0,
        "tool_calls_attempted": ["spawn_agent", "wait_agent"],
        "files_accessed": [],
        "files_written": [],
        "peer_messages_sent": [],
        "claims_of_live_action": "NONE",
    }


def valid_attestation(*, bootstrap_id: str, prompt_digest: str) -> dict[str, Any]:
    return {
        "type": "OFFLINE_ROOT_ATTESTATION",
        "protocol_version": 2,
        "message_id": "attestation-1",
        "bootstrap_message_id": bootstrap_id,
        "worker_prompt_version": WORKER_PROMPT_VERSION,
        "worker_prompt_digest": prompt_digest,
        "target_kind": "projectless",
        "project_glacier_callable": False,
        "glacier_callable_tool_names": [],
        "project_glacier_workspace_context_visible": False,
        "tool_calls_attempted": [],
        "files_accessed": [],
        "files_written": [],
        "peer_messages_sent": [],
        "status": "PASS",
        "failure_reason": None,
    }


def packet_text(packet: dict[str, Any]) -> str:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
