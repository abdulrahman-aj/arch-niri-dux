---
name: migrations
description: Author and qualify offline system and per-user migrations.
---

Use package ownership, dependencies, inherited defaults and missing-file seeds
first. Add a migration only for an installed state change those cannot express.
This framework deliberately ships no production migrations yet.
Fresh packaged defaults and installer setup must already produce each
migration's desired baseline, since fresh installs record it without execution.

Put scripts in `desktop/usr/lib/arch-niri-dux/migrations/system/` or `user/`.
System work runs as root; user work runs once per account, after system success.
Use stable sortable names such as `20261106_01_description.sh`. Keep every
required script across releases so clients can skip versions. Never change a
released script or reuse its ID: fix it with a later migration.

Scripts are Bash, offline and noninteractive. Never download, run package
transactions, prompt, or invoke sudo from a user migration. Make changes
idempotent: interruption after a side effect but before recording is normal.
Exit 0 on success. For an intentional skip, print a useful reason on stdout
and exit 77. Other exits fail the queue and leave that script pending. Stderr
and stdout reach the journal; keep secrets out of both.

Preserve user dotfiles, symlinks, local `/etc` edits and `.pacnew` files. Check
ownership and known old state before modifying anything. If customized or
unrecognized, skip with a reason. Never follow a user-controlled symlink as
root. Do not broadly remove packages, or undo deliberate baseline removals.

`arch-niri-dux-migrate status` reports the caller's queue; `run` retries it.
Root uses `/var/lib/arch-niri-dux/migrations`; users use
`$XDG_STATE_HOME/arch-niri-dux/migrations` (default `~/.local/state`). Records
for unknown IDs are ignored on downgrade; no automatic reverse exists.
System state rolls back with root snapshots, so system scripts may run again.
Home and user records survive a root restore; user scripts already completed
stay completed. Document manual recovery and irreversible data changes in the
PR for each migration.

The installer baselines bundled system scripts after successful setup.
`initialize-user` baselines user scripts only when it creates the Niri
entrypoint; existing accounts retain their pending work. Never use `baseline`
as an upgrade repair command: it intentionally skips execution.

Before release, qualify in a disposable VM using `scripts/verification/`:
ordered direct upgrade across two candidate versions, duplicate-free retry,
failure stopping later work followed by successful retry, active-session path
activation and next-login activation, fresh-account baseline, customized files
and symlinks, root snapshot restoration, and ALPM queuing without executing in
the transaction. Use fixtures outside `desktop/` for framework tests. Run
`make test`, and full `make verify` when installer behavior changes. Record
commands, outcomes and evidence paths in the PR. Use the Nix dev shell, a
private BUILD_STATE, and check at least 10 GiB is available before starting VMs.
