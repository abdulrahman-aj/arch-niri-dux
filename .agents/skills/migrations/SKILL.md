---
name: migrations
description: Author and qualify offline per-user migrations and system state fixes.
---

Prefer package ownership, dependencies, inherited defaults and missing-file
seeds; `/etc/skel` covers new accounts. Add a migration only for state those
cannot express, and only when the task asks to change existing installs. A
changed seed covers new accounts. Ship no production scripts until needed.
Fresh defaults must produce the desired baseline. Fresh installs and accounts
record completion without executing scripts.

User scripts live in `desktop/usr/lib/arch-niri-dux/migrations/*.sh` and run
once per account at graphical login via `arch-niri-dux-migrate.service`.
Name scripts `YYYYMMDD_NN_slug.sh`; the runner rejects other names. Scripts
must be offline, noninteractive and idempotent, without sudo, downloads,
package transactions or prompts. Exit nonzero to stop and stay pending; the
runner records a marker per success in
`~/.local/state/arch-niri-dux/migrations/`. `/etc/skel` carries the same
markers, so `useradd -m` copies a complete set and fresh accounts never
execute. Ship no production scripts until needed. Never edit a released
script or reuse an ID.

System fixes have no queue. Guard on state, not version: use pacman's
`post_upgrade` in a `.install` file (or a PostTransaction hook when the whole
transaction must finish first). Pacman does not retry failed scriptlets, so
state guards self-heal and skip cleanly. No package installs inside, no
`/home` walking as root. Keep fixes safe in the installer chroot. Hardware
fixes are one package-owned idempotent command the installer calls and
`post_upgrade` can re-call.

Qualify with fixtures outside `desktop/` in disposable VMs: ordered retry,
fail-stop, fresh-account markers, preserved custom files. Run `make test`
and full `make verify` for installer changes; record outcomes in the PR.
