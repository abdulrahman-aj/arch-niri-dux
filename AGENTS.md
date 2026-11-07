# Development

Enter the Nix dev shell with `nix develop`. Make targets enter it automatically.

| Targets | Purpose |
| --- | --- |
| `lint`, `fmt`, `test`, `build-test` | Light work: check, format, run unit tests |
| `iso`, `iso-development` | Heavy: build the release or development ISO |
| `lock` | Heavy: resolve package inputs |
| `verify`, `verify-release` | Heavy: full offline acceptance, optionally check published delivery |
| `verify-fast`, `verify-hibernate` | Heavy: qualify package changes on an accepted fixture, or test resume |
| `fetch-alongside`, `provision-alongside`, `verify-alongside` | Heavy: fetch inputs, prepare fixtures, test alongside installs |
| `gc` | Delete storage owned by checkouts that no longer exist |

Pass verification arguments through `ARGS='...'`. Evidence defaults to
`out/verify/`; see [VERIFICATION.md](VERIFICATION.md) for fixture requirements.

[scripts/workspace.py](scripts/workspace.py) gives each checkout its own
`BUILD_STATE` by default; an explicit `BUILD_STATE` wins. It requires 30 GiB
free before builds and VM disk creation. Heavy make targets refuse when
`AGENT_HEAVY_JOBS=deny`.

Run `make gc ARGS=--dry-run` before `make gc`. GC uses ownership markers
and a cache registry, including custom verification paths. It keeps storage owned by live
checkouts, unmarked fixtures and shared downloads. Alongside provisioning
creates reusable fixtures, not checkout-owned states; remove those explicitly.

Open PRs for changes. Never push to main, tag or publish. Never touch physical
disks, boot configuration or firmware.

Read [.agents/skills/migrations/SKILL.md](.agents/skills/migrations/SKILL.md)
before changing migration code or adding a migration.
