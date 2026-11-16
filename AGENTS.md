# Product rules

- The ISO must install the full desktop offline.
- New defaults apply to new installs and accounts. Don't rewrite an existing
  install's settings or state (compatibility code, migrations) unless the task
  asks for it; package updates still reach existing installs.
- Before designing a desktop feature, read the Dux source matching the vendored
  Niri integration (`desktop/README.md` names the version) and check how
  Omarchy solves the same problem.
- Prefer files owned by packages over files written by the installer.

# Development

Enter the Nix dev shell with `nix develop`. Make targets enter it automatically.

| Change | Check |
| --- | --- |
| Docs or CI config | `make lint` (~1 s) |
| Scripts or tests | `make test` (~37 s) |
| Desktop or package content | `make verify-fast` to a kept VM (~50 s) |
| A probe or stage | kept VM, then one full `make verify` |
| Installer, archiso, or release | `make iso-development` (4–7 min) + `make verify` (~8.5 min, ~16.5 with `--slow`) |

While iterating on installer screens, use `make installer-preview` (edit to screen in about a second) instead of
rebuilding the ISO; for desktop files, use the kept `verify-fast` VM. Don't write your own QEMU drivers: drive both
with `python3 -m tools.vm.console`. Previews never qualify a change; the table above still applies to the final head.

`make lint` and `make test` are light: run them before every PR, on any
machine, including one with `AGENT_HEAVY_JOBS=deny` (heavy targets refuse
there). `make qualify` runs the table's tier for the current branch and writes
`out/qualify/<head>/EVIDENCE.md` to paste into the PR.

Pass verification arguments through `ARGS='...'`. Evidence defaults to
`out/verify/`; see [VERIFICATION.md](VERIFICATION.md) for fixture requirements.
For QML/probe-only iteration, keep the fast VM instead of rerunning full
acceptance: [VERIFICATION.md](VERIFICATION.md#debugging-loop-kept-verify-fast-vm).

Keep job logs and scratch evidence under `out/` in your worktree.

[tools/workspace/state.py](tools/workspace/state.py) gives each checkout its own
`BUILD_STATE` by default; an explicit `BUILD_STATE` wins and is yours to manage.

Run `make gc ARGS=--dry-run` before `make gc`. GC uses ownership markers
and a cache registry, including custom verification paths. It keeps storage owned by live
checkouts, unmarked fixtures and shared downloads. Superseded clean-main accepted
fixture disks are collected even in live checkouts; GC keeps the fixture discovery
selects and backing disks referenced by registered overlays, plus text evidence. Alongside provisioning
creates reusable fixtures, not checkout-owned states; remove those explicitly.

Open PRs for changes. Never push to main, tag or publish. Never touch physical
disks, boot configuration or firmware.

Read [.agents/skills/migrations/SKILL.md](.agents/skills/migrations/SKILL.md)
before changing migration code or adding a migration.
