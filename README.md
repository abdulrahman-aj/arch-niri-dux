# arch-niri-dux

Offline, mutable Arch x86_64 with Niri, Dux Shell 2.0+, encrypted Btrfs and
Limine/Snapper recovery. No personal dotfiles, Secure Boot or hibernation.

Build and verify with one command each:

```sh
make iso
make verify
```

These tasks enter the locked Nix shell automatically and enable flakes explicitly.
For an interactive shell after the files are tracked, use
`nix --extra-experimental-features 'nix-command flakes' develop`.
The launcher copies only flake.nix/lock into build state so untracked ISO outputs
are never copied into the Nix store. The build
requires Linux, Nix, Make and accessible `/dev/kvm`. Public signing keys for the
locked inputs are included under `keys/`; UEFI firmware comes from the pinned
Nix shell. It uses no host root privileges and
installs no host packages. The host pacman package cache is mounted read-only.
`BUILD_STATE` defaults to `/tmp/arch-niri-dux-build`; allow approximately 16 GiB
there, 8 GiB guest RAM, and space for the resulting ISO under `out/`.

The pinned, SHA-256 and signature-checked official 2026.10.01 Arch ISO boots a
KVM build guest. Only its disposable virtual disk is formatted. The complete
live, target and build package closure is frozen in `packages/closure.lock.json`
with exact versions, HTTPS origins, SHA-256 hashes and detached signatures.
Official Arch repositories take precedence; the Omarchy package repository
supplies only packages absent from official repositories (including the Limine
integration helpers and Yay). No Omarchy desktop configuration is imported.
Every payload is checked against its digest and signature with trusted host
packager keys bundled in this repository before entering the offline repository. Retain the lock to reuse
the exact closure; deleting it explicitly refreshes package versions. Official packages fall back to the durable Arch package archive when missing
from the current mirror, retaining their pinned hash and signature. Third-party
repository retention can still make an old package unavailable; the builder
fails rather than substituting a version. Rebuilds install the selected build tools
from the verified closure too. An intentional lock refresh initially resolves
against current signed repositories. This is pinned package input assembly, not a byte-reproducible
ISO promise.

Outputs are `out/arch-niri-dux-*.iso`, `out/SHA256SUMS`,
`out/packages.manifest.json` and `out/trusted-packagers.gpg`. Logs and the
reusable builder disk remain under `BUILD_STATE`. `make build-test` runs build
failure-case tests; `make verify` runs the parent-owned acceptance runner.

The ISO exposes its complete local repository at `/run/archiso/bootmnt/repo`,
including `SHA256SUMS` for pre-wipe validation. Installation needs no network.
TTY1 runs the guided `arch-niri-install` wizard; TTY2 is recovery; serial ttyS0
provides a root shell for VM acceptance automation. Installer sources live under
`/usr/local/lib/arch-niri-dux/installer`; the package list and target desktop
overlay live under `/usr/local/share/arch-niri-dux/`. The target overlay supplies
`configure-desktop USERNAME`, which enables the greeter and creates user config
from packaged upstream defaults offline.

Do not install on a laptop until `make verify` has passed the actual ISO's
UEFI boot, offline installation, desktop startup and snapshot recovery checks.
