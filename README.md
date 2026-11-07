# Arch + Niri + Dux

An offline Arch installer for x86_64 UEFI machines, with Niri, Dux 2.1,
encrypted Btrfs and snapshot recovery. The installed system includes
[desktop apps and device backends](desktop/README.md#applications-and-session-defaults).
The live ISO is terminal-only and includes Fastfetch.

## Build and test

Building requires Linux, Nix and Make. Allow at least 30 GiB of free build space and about 2.5
GiB for the ISO. VM verification also requires KVM access. Build state defaults to
`~/.cache/arch-niri-dux-build-<checkout-path-hash>` (`$XDG_CACHE_HOME` when set). Use `BUILD_STATE=/path/with/space make iso` to
change it. An explicit `BUILD_STATE` wins and is yours to manage; GC leaves
it alone. Every build state shares the verified bootstrap ISO and packages in
the shared downloads cache (`$XDG_CACHE_HOME/arch-niri-dux-downloads`, default `~/.cache`). Builds recheck each file's hash or
signature before use. The cache never prunes old versions; deleting it is safe.

```sh
make iso
make verify
```

See [verification](VERIFICATION.md) for test targets, VM requirements and current limits.

`make iso` uses the recipe version's signed release packages. For unreleased
integration changes, use `make iso-development` instead. See [release maintenance](packages/RELEASING.md).

The build writes the ISO and checksums to `out/`.

Signed binary packages and reviewed Dux app recipes use separate pins. See
[package inputs](packages/README.md) and [desktop
configuration](desktop/README.md#niri-configuration) for details. Rebuilding our packages from pinned inputs
does not produce byte-identical packages.

## Get the ISO

Build it yourself with `make iso` (see [Build and test](#build-and-test)); the ISO lands in `out/`.

### Verify a prebuilt ISO

If you got a prebuilt `arch-niri-dux-0.3.0-x86_64.iso` from someone else, verify it before flashing.
Get `SHA256SUMS` and `SHA256SUMS.sig` from whoever shares the ISO, all three files
together. Download the public key separately from this repository's
[`keys/integration-signing.gpg`](https://raw.githubusercontent.com/abdulrahman-aj/arch-niri-dux/v0.3.0-2/keys/integration-signing.gpg)
and save it as `integration-signing.gpg` next to those files.
Take these instructions from this repository, not from whoever sent the ISO: a public key
supplied beside an ISO is not enough without this independent check.

On Linux with GnuPG and GNU coreutils installed, run from that directory:

```sh
echo 'd296f2efe406b0172b0006fb98e9e515ef1e9cf69dc63e6d6694cf572886fe0f  integration-signing.gpg' |
  sha256sum --check --strict &&
gpgv --keyring ./integration-signing.gpg SHA256SUMS.sig SHA256SUMS &&
sha256sum --check --strict SHA256SUMS
```

Flash only if all commands succeed, including a good signature and
`arch-niri-dux-0.3.0-x86_64.iso: OK`. Otherwise don't flash it.
The first check authenticates the complete public certificate against these
instructions. The signature authorizes the exact ISO bytes the project released;
it does not prove that CI built or reproduced them.

Check that the release is the one you meant to install: signatures do not
prevent replay of an older release. `gpgv` does not enforce key expiry or revocation;
use current trusted instructions and follow any [key recovery
advisory](keys/RECOVERY.md#iso-verification-and-recovery) before installing.

## Install

1. Build the ISO, or verify a prebuilt one, as above. Write it to a USB drive and boot it in UEFI
   mode with Secure Boot off.
2. Follow the terminal wizard. **Erase mode erases the selected disk.** Alongside
   mode asks you to select an unallocated GPT region. See
   [supported layouts and verification](ALONGSIDE.md).
3. Remove the USB and reboot. Unlock the disk in the graphical prompt
   and log in. Press Esc to show boot messages. A text prompt appears if graphics
   are unavailable.

Super+Return opens a terminal, Super+Space opens the launcher, and Super+Alt+L
locks the screen.

Use Dux's system updater or `sudo pacman -Syu` for updates.
The installer offers automatic/custom encrypted swap or no hibernation.
With swap enabled, use `systemctl hibernate`. Closing the lid suspends when
undocked and allows continued use on an external display when docked.

## Recover

Boot a known-good snapshot from Limine, log in, and run:

```sh
sudo limine-snapper-restore
```

Restore and reboot into the normal entry. `/home`, including personal config,
stays untouched. After restoring an older snapshot, run a full system update
before installing anything else. The restored system may trust signing keys
that were revoked after the snapshot was taken.

See [snapshot policy](desktop/README.md#system-policy) for transaction snapshots
and recovery safeguards.

## Status

[Verification](VERIFICATION.md) records accepted software paths and hardware
gaps. Track remaining work in [GitHub
issues](https://github.com/abdulrahman-aj/arch-niri-dux/issues).
