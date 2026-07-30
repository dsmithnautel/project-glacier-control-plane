# Provenance

The broker was originally imported into the private Project Glacier control-plane repository from
a clean local `project-glacier-mcp` checkout at:

```text
14881b23b046dc08bdd3d53ad99f6fec63106b08
```

The control-plane import then added standalone documentation and fail-closed rejection of relative
broker state directories. The component was split from control-plane commit
`5b72faf74991e1e7854ddb634543b04d89ce9261` and made the canonical private source.

Only the MCP component's Git history and tracked regular files belong in this repository. The split
must not include the control plane's prompts, runtime templates, ignored challenge state, generated
Codex configuration, credentials, virtual environments, caches, or parent Git metadata.

The canonical repository cannot contain a file that claims its own current commit hash: changing
that file would change the hash. The consuming control plane therefore records the immutable
standalone commit and matching Git tree outside its vendored `mcp_server/` subtree.

No public license or redistribution grant is implied by this provenance.
