# Provenance

## Historical broker baseline

The original `mcp_server/` component was imported from a clean local
`project-glacier-mcp` checkout at:

```text
14881b23b046dc08bdd3d53ad99f6fec63106b08
```

That import copied tracked source only and excluded Git metadata, virtual environments, caches,
generated files, credentials, and runtime state. The control plane subsequently added
standalone documentation and fail-closed rejection of a relative broker state directory.

## Canonical standalone repository

The canonical broker source now lives in the private, company-approved
[`dsmithnautel/project-glacier-mcp`](https://github.com/dsmithnautel/project-glacier-mcp)
repository. It was created with fresh, component-only history so deleted control-plane content
could not follow it.

The control plane keeps a complete Git subtree under `mcp_server/`, so a control-plane clone does
not depend on a second private checkout at runtime. The current immutable source commit is stored
in root-level `MCP_SOURCE_REVISION`; its exact Git tree is stored in `MCP_SOURCE_TREE`. The pin
must remain outside `mcp_server/` because a canonical repository cannot contain its own current
commit hash without changing that hash.

`scripts/verify_mcp_snapshot.py` proves that:

- the vendored working tree has no tracked or non-ignored drift;
- the committed `HEAD:mcp_server` tree equals `MCP_SOURCE_TREE`; and
- when a standalone clone is supplied, `MCP_SOURCE_REVISION^{tree}` also equals that tree.

The verifier performs no fetch and no network access.

## Updating the subtree

1. Make and validate broker changes in the standalone repository.
2. Fetch its immutable full commit into the control-plane clone.
3. Confirm it descends from the current pin unless a reviewed history migration is intended.
4. Review source and dependency differences and all security invariants.
5. Run:

   ```bash
   git subtree merge --prefix=mcp_server <full-commit> --squash \
     --message "Sync MCP snapshot to <full-commit>"
   ```

6. Require these commands to return the same tree ID:

   ```bash
   git rev-parse HEAD:mcp_server
   git rev-parse <full-commit>^{tree}
   ```

7. Update both root pin files.
8. Run:

   ```bash
   python3 scripts/verify_mcp_snapshot.py --source-repo <standalone-clone>
   python3 scripts/validate_repository.py
   ```

9. Rebuild from the committed lock and run every offline MCP and control-plane test.

Never use the live smoke workflow to prove that an import built. It mutates the challenge.

## Publication boundary

Both repositories remain private and contain an intentional internal exact-origin allowlist. No
public/open-source license or external redistribution permission is implied. Credentials, live
challenge state, authentication URLs, transcripts, generated Codex configuration, and local
journals remain outside Git.
