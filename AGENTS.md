# Product rules

- The ISO must install the full desktop offline.
- New defaults apply to new installs and accounts. Don't rewrite an existing
  install's settings or state (compatibility code, migrations) unless the task
  asks for it; package updates still reach existing installs.
- Before designing a desktop feature, read the Dux source matching
  `packages/closure.lock.json` and check how Omarchy solves the same problem.
- Prefer files owned by packages over files written by the installer.

# Development

Enter the Nix dev shell with `nix develop`. Make targets enter it automatically.

| Change | Check |
| --- | --- |
| Docs or CI config | `make lint` (~1 s) |
| Scripts or tests | `make test` (~37 s) |
| Desktop or package content | `make verify-fast` to a kept VM (~50 s) |
| A probe or stage | kept VM, then one full `make verify` |
| Installer, archiso, or release | `make iso-development` (4–7 min) + `make verify` (16–21 min) |

`make lint` and `make test` are light: run them before every PR, on any
machine, including one with `AGENT_HEAVY_JOBS=deny` (heavy targets refuse
there).

Pass verification arguments through `ARGS='...'`. Evidence defaults to
`out/verify/`; see [VERIFICATION.md](VERIFICATION.md) for fixture requirements.
For QML/probe-only iteration, keep the fast VM instead of rerunning full
acceptance: [VERIFICATION.md](VERIFICATION.md#debugging-loop-kept-verify-fast-vm).

Keep job logs and scratch evidence under `out/` in your worktree.

[scripts/workspace.py](scripts/workspace.py) gives each checkout its own
`BUILD_STATE` by default; an explicit `BUILD_STATE` wins and is yours to manage.

Run `make gc ARGS=--dry-run` before `make gc`. GC uses ownership markers
and a cache registry, including custom verification paths. It keeps storage owned by live
checkouts, unmarked fixtures and shared downloads. Alongside provisioning
creates reusable fixtures, not checkout-owned states; remove those explicitly.

Open PRs for changes. Never push to main, tag or publish. Never touch physical
disks, boot configuration or firmware.

Read [.agents/skills/migrations/SKILL.md](.agents/skills/migrations/SKILL.md)
before changing migration code or adding a migration.
