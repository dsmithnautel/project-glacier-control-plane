"""STDIO MCP entry point for Project Glacier."""

from __future__ import annotations

import atexit
import logging
import sys
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .broker import GlacierBroker
from .errors import GlacierError
from .settings import Settings

_INSTRUCTIONS = """Use these tools only for the explicitly authorized Project Glacier challenge.
Call glacier_open_session first and keep exactly one active case. Send each message, reset, and
submission once with a unique operation ID. Never request authentication material. If delivery is
UNKNOWN or a pending evaluation exists, stop mutations and reconcile state. Prepare and verify the
immutable payload hash before commit. API state does not prove browser-only visual signals."""

mcp = FastMCP("Project Glacier", instructions=_INSTRUCTIONS)
_LOGGER = logging.getLogger("project_glacier_mcp")
if not _LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s project_glacier_mcp: %(message)s"))
    _LOGGER.addHandler(handler)
_LOGGER.setLevel(logging.WARNING)
_LOGGER.propagate = False

_broker: GlacierBroker | None = None


def _get_broker() -> GlacierBroker:
    global _broker
    if _broker is None:
        _broker = GlacierBroker(Settings.from_environment())
    return _broker


def _close_broker() -> None:
    global _broker
    if _broker is not None:
        _broker.close()
        _broker = None


atexit.register(_close_broker)


def _invoke(tool_name: str, action: Callable[[GlacierBroker], dict[str, Any]]) -> dict[str, Any]:
    broker: GlacierBroker | None = None
    try:
        broker = _get_broker()
        return action(broker)
    except GlacierError as error:
        if broker is not None:
            return broker.error_result(error)
        return {"ok": False, "error": error.as_dict(), "active_case_id": None}
    except Exception as error:  # noqa: BLE001 - boundary sanitizes unexpected failures
        _LOGGER.error("Unexpected %s failure (%s)", tool_name, type(error).__name__)
        case_state = broker.current_case_state if broker else None
        delivery_unknown = case_state in {
            "CHAT_DELIVERY_UNKNOWN",
            "CLEAR_DELIVERY_UNKNOWN",
            "SUBMISSION_DELIVERY_UNKNOWN",
            "RECOVERY_REQUIRED",
        }
        return {
            "ok": False,
            "error": {
                "code": "DELIVERY_UNKNOWN" if delivery_unknown else "INTERNAL_ERROR",
                "message": (
                    "A state-changing operation may have reached the challenge service."
                    if delivery_unknown
                    else "The local MCP broker encountered an unexpected error."
                ),
                "retryable": False,
                "delivery_status": "UNKNOWN" if delivery_unknown else "NOT_SENT",
                "next_action": (
                    "Do not retry or send another mutation; reconcile state first."
                    if delivery_unknown
                    else "Stop live writes and inspect the local broker logs."
                ),
                "case_state": case_state,
            },
            "active_case_id": broker.active_case_id if broker else None,
        }


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def glacier_open_session() -> dict[str, Any]:
    """Authenticate locally, verify the API schema, and recover any pending evaluation."""

    return _invoke("glacier_open_session", lambda broker: broker.open_session())


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def glacier_start_case(
    experiment_id: str,
    category: str,
    policy_subcategory: str = "",
) -> dict[str, Any]:
    """Start one clean, approved case after confirming that no prior case is pending."""

    return _invoke(
        "glacier_start_case",
        lambda broker: broker.start_case(experiment_id, category, policy_subcategory),
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def glacier_send_message(
    case_id: str,
    message: str,
    operation_id: str,
) -> dict[str, Any]:
    """Send one pre-approved turn exactly once; never retry an UNKNOWN result."""

    return _invoke(
        "glacier_send_message",
        lambda broker: broker.send_message(case_id, message, operation_id),
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def glacier_get_state(
    case_id: str | None = None,
    sync_remote: bool = True,
) -> dict[str, Any]:
    """Read local case state and optionally synchronize the server pending state."""

    return _invoke(
        "glacier_get_state",
        lambda broker: broker.get_state(case_id, sync_remote=sync_remote),
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def glacier_prepare_submission(
    case_id: str,
    outcome: str,
    case_summary: str,
) -> dict[str, Any]:
    """Prepare and hash an immutable evaluation preview without a network mutation."""

    return _invoke(
        "glacier_prepare_submission",
        lambda broker: broker.prepare_submission(case_id, outcome, case_summary),
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def glacier_commit_submission(
    case_id: str,
    payload_hash: str,
    operation_id: str,
) -> dict[str, Any]:
    """Commit the exact prepared evaluation once when external submission is enabled."""

    return _invoke(
        "glacier_commit_submission",
        lambda broker: broker.commit_submission(case_id, payload_hash, operation_id),
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def glacier_reset_case(
    case_id: str,
    transcript_hash: str,
    operation_id: str,
) -> dict[str, Any]:
    """Clear or close the intended case; pending terminal cases cannot be reset."""

    return _invoke(
        "glacier_reset_case",
        lambda broker: broker.reset_case(case_id, transcript_hash, operation_id),
    )


def main() -> None:
    """Run the local MCP server over STDIO."""

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
