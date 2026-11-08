# Arch + Niri + Dux

An offline installer that sets up Arch Linux with the Niri compositor and the Dux desktop shell on an
encrypted, snapshotted disk. Signed updates and migrations keep installed machines current after setup.

The desktop software comes from upstream: Arch, Niri, Dux, Limine and snapper. This project chooses the
defaults, wires the pieces together, tests the result in a VM before every release and ships fixes to
machines that are already installed.

## What you get

- A Niri and Dux desktop with its [apps and device support](desktop/README.md#applications-and-session-defaults)
  set up, including printing, the camera, screen sharing, a keyring that unlocks at login, and hibernation.
- Full-disk encryption (LUKS2 with Argon2id) on Btrfs.
- A snapshot before every package change. You can boot an older snapshot from the boot menu and restore it.
- A firewall that drops unsolicited inbound connections and still accepts Tailscale.
- A screen that locks before suspend and goes dark when you close the lid.
- An install that checks package signatures, and releases signed with a project key.
- Updates through Dux's updater. When a release must change existing machines, a migration runs once on each
  machine or account during the update.
- Your own settings in `~/.config/niri/overrides.kdl` and Dux Settings. Updates change the defaults, never
  your files.

Every release installs in a VM and passes the [acceptance checks](VERIFICATION.md) first, from disk unlock
through suspend, updates and snapshot restore.

## What it isn't

- A general Arch installer. It installs this one desktop with these choices.
- Mature. It is young, and only x86_64 UEFI machines are supported.
- Frozen. It follows the live Arch repositories and takes a few packages from the Omarchy repository.

## Install

1. Get the ISO. Build it with `make iso` (see [BUILDING.md](BUILDING.md)), or [verify a prebuilt
   ISO](#verify-a-prebuilt-iso) that someone gave you.
2. Write it to a USB drive and boot it in UEFI mode with Secure Boot off.
3. Follow the terminal wizard. **Erase mode erases the selected disk.** Alongside mode asks you to pick an
   unallocated GPT region. See [supported layouts](ALONGSIDE.md).
4. Remove the USB drive and reboot. Unlock the disk in the graphical prompt and log in. Press Esc to show
   boot messages. A text prompt appears if graphics are unavailable.

Super+Return opens a terminal, Super+Space opens the launcher, and Super+Alt+L locks the screen.

The installer offers automatic or custom encrypted swap, or no hibernation. With swap, use
`systemctl hibernate`. Closing the lid suspends the laptop when it is undocked. Docked, it keeps running on
the external display.

### Verify a prebuilt ISO

If someone gave you `arch-niri-dux-0.3.1-x86_64.iso`, verify it before flashing. Get `SHA256SUMS` and
`SHA256SUMS.sig` from the same person, all three files together. Download the public key separately from
this repository,
[`keys/integration-signing.gpg`](https://raw.githubusercontent.com/abdulrahman-aj/arch-niri-dux/v0.3.1-1/keys/integration-signing.gpg),
and save it as `integration-signing.gpg` next to those files. Take these instructions from this repository,
not from whoever sent the ISO. A public key that arrives with the ISO proves nothing on its own.

On Linux with GnuPG and GNU coreutils, run this in that directory:

```sh
echo 'd296f2efe406b0172b0006fb98e9e515ef1e9cf69dc63e6d6694cf572886fe0f  integration-signing.gpg' |
  sha256sum --check --strict &&
gpgv --keyring ./integration-signing.gpg SHA256SUMS.sig SHA256SUMS &&
sha256sum --check --strict SHA256SUMS
```

Flash the ISO only if every command succeeds, including a good signature and
`arch-niri-dux-0.3.1-x86_64.iso: OK`. The first command checks the whole public key against these
instructions. The signature shows the project released these exact ISO bytes. It does not show that CI
built or reproduced them.

Make sure it is the release you meant to install, because signatures don't stop someone from handing you an
older one. `gpgv` doesn't check key expiry or revocation, so follow any [key recovery
advisory](keys/RECOVERY.md#iso-verification-and-recovery) before installing.

## Update

Click the update button next to the clock when it appears, or run `sudo pacman -Syu`. Both run any pending
migrations.

## Recover

Boot a working snapshot from the Limine menu, log in, and run:

```sh
sudo limine-snapper-restore
```

Restore it and reboot into the normal entry. The restore leaves `/home`, including your personal config,
as it was. After restoring an older snapshot, run a full system update before installing anything else,
because the restored system may trust signing keys that were revoked after the snapshot. The [snapshot
policy](desktop/README.md#system-policy) covers transaction snapshots and other safeguards.

## Build and contribute

[BUILDING.md](BUILDING.md) covers building the ISO and running the VM tests. [Verification](VERIFICATION.md)
records what the tests cover and which hardware checks remain. Open work lives in [GitHub
issues](https://github.com/abdulrahman-aj/arch-niri-dux/issues).
