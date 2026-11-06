---
name: migrations
description: Author and qualify offline system and per-user migrations.
---

Prefer package ownership, dependencies, inherited defaults and missing-file
seeds. Add a migration only for state changes those cannot express. Ship no
production scripts until needed. Fresh defaults must produce the desired
baseline. Fresh installs and accounts record completion without executing
scripts.

Place Bash scripts in `desktop/usr/lib/arch-niri-dux/migrations/{system,user}/`.
System scripts run as root; user scripts run per account after system success.
Name scripts `YYYYMMDD_NN_slug.sh`, e.g. `20261106_01_description.sh`; the
runner rejects other names so text order stays chronological. Retain scripts
across releases for skipped-version upgrades. Never edit a released script or reuse an
ID. Scripts must be offline, noninteractive and idempotent. Interruption can
occur between side effects and recording. No downloads, package transactions,
prompts, or sudo from user scripts. Exit 0 for success; exit 77 with a stdout
reason to record a skip. Other exits stop the queue, leaving the failed script
pending. Output reaches the journal; never print secrets.

Preserve dotfiles, symlinks, local `/etc` edits and `.pacnew` files. Check ownership
and known old state first; skip customized or unrecognized state with a reason.
Never follow user-controlled symlinks as root or undo deliberate package removals.

`arch-niri-dux-migrate status` inspects the caller's queue; `run` retries it.
Root records live in `/var/lib/arch-niri-dux/migrations`; user records live in
`$XDG_STATE_HOME/arch-niri-dux/migrations`, defaulting to
`~/.local/state/arch-niri-dux/migrations`. Unknown IDs are ignored on downgrade;
nothing reverses automatically. Root snapshots roll back system records, so
scripts may re-run; home and user records survive. Document irreversible changes
and manual recovery in each migration's PR. The installer baselines system
scripts; `initialize-user` baselines only when creating a fresh Niri entrypoint.
Never use `baseline` to repair an upgrade.

Qualify with fixtures outside `desktop/` in disposable VMs: ordered direct
upgrade across two candidates, duplicate-free retry, fail-stop and recovery,
active-session .path and next-login activation, fresh-account baseline,
preserved custom files and symlinks, root snapshot restore and ALPM deferral.
Run `make test` and full `make verify` for installer changes; record outcomes
and evidence in the PR. Use the Nix dev shell and a private `BUILD_STATE`. Run
VMs on `msi-dux` with at least 10 GiB available RAM before starting each 8 GiB
guest.
