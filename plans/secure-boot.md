# Secure Boot

**Status (2026-11-12):** draft design, not started. Opus and Sol review it independently before phase 1;
open decisions are in section 11 and in [#49](https://github.com/abdulrahman-aj/arch-niri-dux/issues/49).

**Decided so far:** handle boot-chain tampering as a whole in this work, with no interim mitigations
(2026-11-07). Proposed: own keys per machine with `sbctl` (Microsoft's certificates kept), sign only the
Limine loader, and enable it with one command on the installed system.

The rest of this file is the design draft (Fable 5.2, 2026-11-11).

---


Status: complete (see the last line).

Design only: no repo changed, no PR, no VM run. Reviewed main at 4f61ea0.
*Verify* marks a fact the implementer must confirm in a VM before relying on it. Nothing here was run under an
enforcing firmware.
Inputs: `fable-architecture.md`, `fable-risk.md`, issue #49 and its comments, the 2026-11-07 Opus draft
(`design-49-secure-boot.md`), the Limine tooling installed on the laptop (limine 12.9.6, limine-mkinitcpio-hook
1.40.0, limine-snapper-sync 1.32.1), the sbctl 0.18 man page, and Omarchy's repos and issue tracker.

## 1. Recommendation

1. **Own keys per machine with sbctl, Microsoft certificates kept (`sbctl enroll-keys -m`).** No shim, no MOK.
2. **Sign one file: the Limine loader.** Limine's enrolled config hash covers `limine.conf`, and the BLAKE2 hash
   on every path in it covers kernels, initramfs, command lines and snapshot entries. No UKIs, no change to
   `mkinitcpio.conf.d`. The upstream boot hooks already re-seal and re-sign on every kernel, Limine and snapshot
   change; we add no signing hook of our own.
3. **One package command, `arch-niri-dux-secure-boot {status,enable}`, run on the installed system.** The
   installer does not enroll keys in v1. A new install and an existing install take the same path, and it can
   be developed on a kept `verify-fast` VM without rebuilding the ISO.
4. **The fallback loader `EFI/BOOT/BOOTX64.EFI` stays the stock, unsigned, unsealed Limine.** Firmware refuses it
   while Secure Boot is on, so it is not a bypass. With Secure Boot off it is the rescue loader. Every failure in
   section 7 is then recoverable by turning Secure Boot off in firmware, without a USB stick.
5. **Do not enforce kernel module signatures or lockdown.** Arch's kernel does not, so DKMS NVIDIA modules and
   hibernation keep working with no signing path.
6. **Windows:** start it through Limine's `efi_boot_entry` (firmware `BootNext` plus a reset), not a chainload.
   Keep refusing BitLocker at install time for now.
7. **Be honest about the claim.** This protects our own boot files against offline edits and satisfies software
   that demands Secure Boot (FACEIT). With Microsoft's certificates in `db` it is not a complete evil-maid
   defense; that needs TPM-bound unlock, which is a follow-up.

Where this differs from the 2026-11-07 draft: enablement is a command and not an installer step; the fallback
stays unsigned as the rescue path (the draft wanted a signed sealed copy); keys stay inside `@` on purpose;
firmware key enrollment is the last step and is read back; the threat-model claim is narrower; and Omarchy's
tracker now supplies real failure reports that shape sections 5 and 7.

## 2. Evidence

| Fact | Source |
|---|---|
| Limine hardens only when Secure Boot is on, Setup Mode is off, **and** a config hash is enrolled in the loader. Then: config checked against the hash, every path must carry `#<blake2b>`, editor and UEFI shell disabled, hash mismatch panics, measured boot forced on when a TPM exists. A signed loader without an enrolled hash "provides no integrity guarantees". `protocol: efi` targets are exempt from hashes because firmware verifies them. | `/usr/share/doc/limine/USAGE.md:28-56`, `CONFIG.md:152-160, 257-259, 446-454` |
| `efi_boot_entry` exists in 12.9.6: `entry: <firmware entry description>`; sets `BootNext` and resets. | `CONFIG.md:271, 360-361` |
| The tool enrolls only when `ENABLE_ENROLL_LIMINE_CONFIG=yes` is in `/etc/default/limine`; the variable is cleared after every other config file is read. | `/usr/lib/limine/limine-common-functions:143-149` |
| Seal cycle, run by `limine-install`, `limine-mkinitcpio`, `limine-snapper-sync` and restore: pre-hook `10-limine-reset-enroll` puts back the pristine loader from `limine_x64.bak`; post-hook `90-limine-enroll-config` runs `limine enroll-config <loader> <b2sum of limine.conf>`, then `sbctl sign <loader>` when sbctl reports keys installed. | same file `:281-376`; `/etc/boot/hooks/{pre,post}.d` |
| `ENABLE_VERIFICATION=yes` (per-file hashes) is the stock default in `/etc/limine-entry-tool.conf`; our drop-in does not set it. | that file; `desktop/usr/share/limine-entry-tool.d/arch-niri-dux.conf` |
| `limine-install` rewrites the fallback from the stock binary whenever they differ, and never signs it. Upstream notes the same. | `/usr/bin/limine-install:447-484`; entry-tool README `:461` |
| Arch kernel: `MODULE_SIG=y`, `MODULE_SIG_FORCE` unset, `LOCK_DOWN_KERNEL_FORCE_NONE=y`. Secure Boot does not turn on lockdown or module signature enforcement. | `/proc/config.gz` on the laptop (7.2.14-arch1) |
| sbctl 0.18-2 is in `extra` (10 MiB). `enroll-keys` has `-m`, `-f` (firmware built-in db/KEK), `-a`, `--partial`, `--export`. It does not touch `dbx`. No `shim-signed` package in Arch. | `pacman -Si`; sbctl 0.18 `sbctl.8.txt` |
| Our installer refuses when `SecureBoot` reads 1 and ignores `SetupMode`. Alongside mode refuses BitLocker and writes the old system's entry into `limine.conf` by hand as `protocol: efi`. | `installer/install.py:566-568`; `installer/storage.py:271-276, 394-402` |
| The VM harness uses nixpkgs `OVMF.fd` (no Secure Boot build), q35 without SMM. `swtpm` is in the dev shell and already used by the Windows fixture. | `flake.nix:10-11`; `scripts/verification/vm.py:119-157`; `scripts/alongside_windows.py` |
| **Omarchy #10945:** a second hook copied the stock loader over the signed one after a Limine-only update; machines stopped at "Secure Boot Violation". sbctl's own `zz-sbctl.hook` did not catch it. | omacom/omarchy issue, 3 machines |
| **Omarchy #15524:** a migration edited `/boot/limine.conf` with `sed` and did not re-enroll; machines halted at `CHECKSUM MISMATCH FOR CONFIG FILE`. One reporter: disabling Secure Boot did not get past it; recovery took three hours with a live USB. | omacom/omarchy issue |
| **Omarchy #12045:** on an MSI X870E board, Limine `protocol: efi` (UKI) fails under Secure Boot with `LoadImage` access denied even when signed; `protocol: linux` (`ENABLE_UKI=no`) works. The same board silently ignored `db`/`KEK` writes in Setup Mode. | omacom/omarchy issue |
| **Omarchy #16927:** a chainloaded Windows makes BitLocker ask for the recovery key after Limine changes; `efi_boot_entry` did not, on one ASUS laptop. The entry name must match the firmware description exactly; only `BootOrder` entries are searched; first match wins. | omacom/omarchy issue |

## 3. Omarchy: status and approach

**Shipped today: nothing.** The install guide says to turn Secure Boot off. The ISO is unsigned. The installed
system has no key or signing setup. Code search over `omarchy`, `omarchy-iso` and `omarchy-pkgs` finds `mokutil`
and shim only in a plan file. A maintainer wrote on 2026-10-19 (omarchy-iso #289): "Secure Boot support is
planned"; the issue stays open to track it.

**What their users do meanwhile:** sbctl own keys with `-m` after install, relying on the same
limine-mkinitcpio-hook we use (sbctl is its optional dependency). The four issues in section 2 come from that
path. It works, and its failures are the ones this design is built around.

**Their plan** (`omarchy-iso/plans/consumer-secure-boot.md`, DHH, written 2026-04-30, revised 2026-08-15):

- Goal: install on consumer laptops without touching firmware keys.
- Chain: their own Microsoft-signed shim → MokManager once → a signed second stage → signed UKI. Per-machine MOK
  key on the encrypted root signs UKIs at install and on updates. The ISO is signed by a release key.
- Second stage: systemd-boot for Secure Boot installs "unless Limine's signature enforcement is proven"; Limine
  stays for everyone else.
- Critical path, by their own account: an EV certificate and a `shim-review` submission, "weeks to months", with
  standing revocation duties. No sign in the repos that it has started.
- Open in their plan: DKMS module signing (they call it a v1 blocker), snapshot boot when each kernel must be a
  signed UKI, recovery when MOK enrollment is skipped.
- BitLocker policy already shipped: tell users to decrypt, not suspend (omarchy-iso PR #105).

**What to take from it:**

- Their goal differs from ours. They want zero firmware visits on factory-keyed laptops. We want Secure Boot to
  stay on for a few known machines, including a Windows dual-boot PC. Own keys solve ours now with no outside
  dependency.
- Their two open problems do not exist in our design: snapshots are covered by the config hash, and module
  signing is not needed (5.7).
- Their note that unsigned modules "will not load" under Secure Boot is true for shim-based distro kernels with
  lockdown, not for Arch's kernel.
- If they ship a shim, we can put it in front of the same sealed Limine later (section 12). Nothing here blocks
  that.
- Memory says #49 should wait for their work. I recommend not waiting: their path depends on Microsoft and has
  no date. Decision D1.

## 4. Options

### Trust anchor

| | Own keys, sbctl `-m` (recommended) | Shim + MOK | Own keys only, no Microsoft certs |
|---|---|---|---|
| Firmware visit | One: clear keys / Setup Mode | None in firmware; one MokManager screen | One |
| Outside dependency | None | A Microsoft-signed shim. Arch has none; borrowing Fedora's or Ubuntu's ties us to their SBAT revocations; our own needs an EV cert and shim-review | None |
| Limine fit | Works today | Shim refuses binaries without an SBAT section; Limine has none, so we would add one before signing | Works |
| Windows dual-boot | Works: Microsoft certs stay in `db` | Works | Windows stops booting |
| Option ROMs (discrete GPUs) | Covered by `-m` | Untouched | Risk of no display or a bricked board |
| Unsigned ISO boots | In Setup Mode or with Secure Boot off | Needs a signed ISO or Secure Boot off | Same as first column |
| Evil-maid strength | Medium: any Microsoft-signed loader still runs | Medium, plus MOK-enrollment phishing | High |
| Cost | Small | Large, recurring | Small, but only safe on laptops with integrated graphics |

### What gets signed

| | Sealed Limine + hashes (recommended) | Signed UKIs |
|---|---|---|
| Signed files | 1 | Loader plus one UKI per kernel and per bootable snapshot |
| Snapshot entries | Covered by the config hash, per-entry command line (`noresume`) | Each needs its own signed UKI or a command line taken from the config |
| Known firmware trouble | None on this path (`protocol: linux`, no `LoadImage`) | MSI `LoadImage` failure (#12045); long-filename firmware bugs |
| Space and dedup | Unchanged | sbctl signatures are not reproducible, so snapshot dedup breaks (snapper-sync README `:656`) |
| Trust in | Limine's hash enforcement, which changed across 11.x and 12.x | The firmware's signature check, widely used |
| Change to our boot policy | One line per machine | `mkinitcpio.conf.d`, entry tool, ESP size, snapshot flow |

The weakness of the recommended column is that everything rests on Limine's own verification. Section 8 tests
exactly that (tamper tests), and the weekly refresh run should include them, because the behavior has changed
before.

### When keys are enrolled

| | Command on the installed system (recommended) | Installer step in Setup Mode | Automatic whenever Setup Mode is seen |
|---|---|---|---|
| Installer change | Wording only | Wizard question, config key, chroot run | Same, no question |
| Firmware visits | One (clear keys before installing, run the command after first boot) | One | One |
| Test loop | Kept VM, about a minute | ISO build plus full verify | Same |
| Existing installs | Same path | Need the command anyway | Need the command anyway |
| Risk | Snapshots taken before enabling cannot be restored with Secure Boot on (section 7, case E) | None extra | Every VM and every board that ships in Setup Mode gets keys nobody asked for |

The installer step can be added later as one call to the same command.

## 5. Recommended design

### 5.1 Chain of trust

```
firmware db (our db cert + Microsoft certs)
  └─ verifies  EFI/limine/limine_x64.efi      signed with /var/lib/sbctl/keys/db, carries b2sum(limine.conf)
       └─ verifies  /boot/limine.conf          against the enrolled hash
            └─ verifies  each kernel, initramfs and snapshot kernel by its #blake2b suffix; cmdlines are in the config
                 └─ kernel + initramfs ask for the LUKS passphrase

EFI/BOOT/BOOTX64.EFI   stock Limine, unsigned, unsealed: refused while Secure Boot is on; rescue loader when off
Windows / other OS     entry with protocol efi_boot_entry: firmware starts it directly after a reset
```

Keys: `sbctl create-keys` writes PK, KEK and db keys to `/var/lib/sbctl/keys`, inside `@` on the LUKS root,
root-only. One key set per machine, never rotated in place. Nothing secret is on the ESP or the ISO.

### 5.2 Changes by file

Boot policy stays in the three places the architecture review named, plus one command.

| File | Change | Reaches |
|---|---|---|
| `packages/packages.target` | add `sbctl` | ISO payload; existing installs through the baseline package |
| `desktop/usr/share/limine-entry-tool.d/arch-niri-dux.conf` | add `ENABLE_VERIFICATION=yes`. It is today's effective value; pinning it means a changed upstream default cannot silently drop the hashes | all installs, no behavior change |
| `desktop/etc/mkinitcpio.conf.d/arch-niri-dux.conf` | none | — |
| `/etc/default/limine` (written by `install.py` `write_boot_config`) | the command appends `ENABLE_ENROLL_LIMINE_CONFIG=yes`. `write_boot_config` itself is unchanged in v1 | machines that ran `enable` |
| `desktop/usr/bin/arch-niri-dux-secure-boot` | new, about 150 lines | package |
| `desktop/usr/share/libalpm/hooks/zzz-arch-niri-dux-secure-boot.hook` | new, about 10 lines: after every transaction run `arch-niri-dux-secure-boot status --after-transaction` | package |
| `installer/install.py` | wording at `:193` and `:568`; one closing line when the firmware is in Setup Mode | new installs |
| `installer/storage.py` `finish_alongside_boot` | write the old system's entry as `efi_boot_entry` (5.6) | new alongside installs |
| `scripts/verification/` | Secure Boot firmware option and one stage (section 8) | — |
| `README.md` | "Turn on Secure Boot" and "If the machine does not boot" (section 7) | — |

Not changed: `ENABLE_UKI=no`, `ENABLE_LIMINE_FALLBACK=yes`, the mkinitcpio hooks, the live ISO, the subvolume
layout, the existing boot-policy hook.

### 5.3 The command

Python, standard library only, so the decision logic has unit tests.

**`status`** prints one line per check and exits 0 (enforcing and sealed), 1 (not enabled: no keys), or 2
(enabled but something is wrong, with the fix). Checks:

1. Firmware: `SecureBoot` and `SetupMode` variables.
2. Keys exist (`sbctl setup --print-state --json`).
3. `ENABLE_ENROLL_LIMINE_CONFIG=yes` is in `/etc/default/limine`; both boot hooks exist and are executable.
4. The loader verifies against our key (`sbctl verify`).
5. The loader contains the hash of the current `limine.conf` (`b2sum` hex found in the loader bytes). *Verify*
   that `limine enroll-config` stores the hash as ASCII hex; if not, compare against a scratch copy enrolled
   the same way.
6. Every `path:` and `module_path:` under a `linux` entry carries a `#` hash.

`status --after-transaction` is silent and exits 0 when keys do not exist. When they do and a check fails, it
runs `limine-enroll-config` once, checks again, and on a second failure prints a warning that says not to
reboot and names the fix. This closes the Omarchy #10945 class (something rewrote the loader late in a
transaction) without relying on `zz-sbctl.hook`.

**`enable`** does the firmware write last, so every earlier failure leaves a machine that still boots:

1. Preflight, no writes. Stop with a clear message unless: root; UEFI; `SetupMode` is 1; the hooks from check 3
   exist; `ENABLE_UKI` is not `yes`; `/boot` is the ESP and holds the only `limine.conf`.
2. If any disk has a BitLocker partition, stop unless `--bitlocker-suspended` is given; print the Windows
   command (5.6).
3. `sbctl create-keys`, skipped when keys exist.
4. Append the enroll line to `/etc/default/limine`.
5. `limine-update`: regenerates the menu; the post-hook seals and signs the loader.
6. Run status checks 3–6. On failure remove the line from step 4, run `limine-update` again, exit 1.
7. `sbctl enroll-keys -m`.
8. Read back: `SetupMode` is now 0 and `sbctl list-enrolled-keys` shows our certificate. If not (the MSI case in
   #12045), say that the firmware did not take the keys, point to the manual path (section 9), exit 2. The
   machine still boots: the loader is sealed and signed but nothing enforces yet.
9. Take a snapper snapshot "Secure Boot enabled". List older snapshots and say they cannot be restored while
   Secure Boot is on (section 7, case E).
10. Print: reboot; switch Secure Boot on in firmware if it is not; run `status`.

No `disable` subcommand. Turning Secure Boot off is a firmware switch, and a sealed, signed loader keeps
booting with it off.

### 5.4 Updates: what re-seals when

No new signing hook. Each row ends in the upstream post-hook (enroll hash, then `sbctl sign`).

| Event | Trigger already in place | What changes on the ESP |
|---|---|---|
| Kernel or initramfs-relevant package | `90-mkinitcpio-install.hook` → `limine-mkinitcpio` | kernel, initramfs, hashes in `limine.conf`, loader re-sealed |
| DKMS module rebuild (NVIDIA) | `70-dkms-install.hook` runs before the row above | new initramfs with the module, same seal |
| `limine` or the hook package | `80-limine-efi-deploy.hook` → `limine-install` | new loader, re-sealed; fallback refreshed, left unsigned |
| Our boot policy files | `95-arch-niri-dux-boot-policy.hook` → `limine-mkinitcpio` | as the kernel row |
| Snapshot created or deleted (snap-pac, cleanup timer) | snapper plugin → `limine-snapper-sync` | snapshot entries and kernel copies, loader re-sealed. *Verify* that the plugin path runs the boot hooks |
| `limine-snapper-restore` | the tool | as above, with the keys of the snapshot being restored |
| End of every transaction | new `zzz-` hook | nothing unless a check fails |

Two rules follow for everyone who touches the repo:

- **Nothing may write `/boot/limine.conf` or the loader except the Limine tools.** Settings go in
  `/etc/default/limine` or a `limine-entry-tool.d` drop-in, followed by `limine-update`. A hand edit on a sealed
  machine is a boot panic (Omarchy #15524). The one hand-written block we have, the alongside entry, is written
  before sealing; any later change to it must end with `limine-enroll-config`. State this in `AGENTS.md`.
- Between the pre-hook and the post-hook the ESP holds an unsigned loader. For `limine-mkinitcpio` that window
  is the length of the initramfs build. A power cut inside it is case A in section 7. I would not fight the
  tool over this.

### 5.5 Installer and offline ISO

- The ISO stays unsigned; archiso's releng profile has no Secure Boot support and a signed ISO needs a shim.
  It boots when Secure Boot is off or the firmware is in Setup Mode. `sbctl` is in the payload, and it embeds
  Microsoft's certificates, so `enable` works offline. *Verify* that `enroll-keys -m` makes no network call and
  that `db` then lists the 2023 Microsoft certificates (the 2011 ones expired this year).
- Installer: keep refusing when `SecureBoot` reads 1, with new text: the installer is unsigned; turn Secure Boot
  off or clear its keys, install, then run the command. When `SetupMode` reads 1, the closing message adds:
  "Firmware is in Setup Mode. After the first boot run `sudo arch-niri-dux-secure-boot enable`."
- Shortest path for a new machine: clear Secure Boot keys in firmware once, install, boot, run `enable`,
  reboot. One firmware visit.

### 5.6 Windows and BitLocker

- **Entry.** `finish_alongside_boot` writes `protocol: efi_boot_entry` and `entry: <description of the chosen
  firmware entry>`. The installer already holds that description. If two `BootOrder` entries share it, keep
  today's chainload entry and say so. This is useful without Secure Boot too (Limine drops out of Windows'
  boot measurements), so it can land first. The Debian fixture covers it: its Microsoft-signed shim must still
  start under our keys.
- **Why not chainload.** Under Secure Boot a chainloaded Windows sees our certificate in its PCR 7 measurements
  and Limine in PCR 4. BitLocker then asks for the recovery key, once or on every Limine update.
- **Enrolling keys changes PCR 7 by itself**, and so does clearing keys to reach Setup Mode. On a machine whose
  Windows has BitLocker or device encryption on, the user must suspend it in Windows before the firmware visit
  (`manage-bde -protectors -disable C: -RebootCount 0`) and resume it after Windows has booted once with the
  new keys. `enable` only checks and explains; it never touches Windows.
- **Install next to BitLocker stays refused in v1**, as in Omarchy. The user said yes to supporting it on
  2026-11-07. I recommend a separate issue after this lands: it needs a Windows-with-TPM fixture and has its
  own failure modes. Decision D6.
- **Existing dual-boot desktop.** Its entry is a chainload and stays one under the product rule. Change it by hand before
  running `enable`, or accept it if BitLocker is off there.
- **FACEIT** needs Secure Boot on in Windows. Whether it accepts a custom PK with Microsoft's certificates kept
  is not confirmed. Check on the real machine before promising it.

### 5.7 NVIDIA and module signing

- Nothing to sign. Arch's kernel loads unsigned and DKMS-signed modules under Secure Boot (section 2). That
  covers the vendored `nvidia-580xx-dkms` and prebuilt `nvidia-open` alike. The kernel is marked tainted, as it
  is today for any out-of-tree module.
- The initramfs that carries the modules for early KMS is covered by its hash. DKMS builds before mkinitcpio,
  so the sealed initramfs contains the fresh module.
- **Invariant to write down:** never add `module.sig_enforce=1` or `lockdown=` to the command line. Either one
  breaks DKMS NVIDIA, and lockdown also disables hibernation. Without shim there is no way to make the kernel
  trust a local module key (the `.machine` keyring is filled from MOK), so enforcement would force the shim
  design. Module enforcement defends against root, which this design does not claim to do.

## 6. Threat model

| Attacker | Before | With this design |
|---|---|---|
| Edits the kernel, initramfs, command line or `limine.conf` on the ESP while the machine is off, from a USB stick or from Windows | Owns the next unlock | Boot stops at a Limine panic or a firmware refusal |
| Uses the Limine editor at the keyboard | Can change the command line | Editor is off on the sealed loader |
| Replaces our loader with another Microsoft-signed chain (an old shim, a Windows PE image) that shows a fake passphrase prompt | Not needed | **Still possible.** Firmware trusts Microsoft's certificates. A cleared `dbx` makes it easier |
| Switches Secure Boot off in firmware, or resets the firmware, then edits the ESP | Not needed | **Still possible and silent** unless a firmware password stops it. Nothing tells the user that Secure Boot went off |
| Has root on the running system | Owns the machine | Unchanged: root holds the signing key |
| Hardware keylogger, DMA, firmware implant | Out of scope | Out of scope |
| Steals the powered-off disk | Stopped by LUKS | Unchanged |

What the user gets: our own boot files cannot be changed quietly; the casual and the Windows-malware version of
the attack stop working; Secure Boot can stay on for Windows. What they do not get: proof that the prompt on
screen is ours. That needs the disk key bound to the TPM's measurements (Limine already extends PCR 8 and 9 when
sealed), so a foreign chain cannot unlock or cannot show a secret. I recommend recording it as the follow-up
and closing the 2026-11-07 threat-model decision with this narrower claim. Decision D7.

Cheap hardening the user does by hand: a firmware administrator password, and USB boot disabled.

## 7. When it does not boot

Every case starts the same way: **switch Secure Boot off in firmware.** Keys stay enrolled, so switching it
back on later needs no Setup Mode.

| Case | Symptom | Cause | Way back |
|---|---|---|---|
| A | "Secure Boot violation", or the firmware skips to Windows | Loader left unsigned: an update was cut between reset and seal | Secure Boot off; the normal entry boots; `sudo limine-update`; Secure Boot on |
| B | Limine: `CHECKSUM MISMATCH FOR CONFIG FILE` | `limine.conf` changed without a re-seal | Secure Boot off; firmware boot menu → the disk's fallback entry (stock loader); `sudo limine-update`; Secure Boot on. *Verify* that a sealed loader still panics with Secure Boot off, as one Omarchy user reported; if it does not, the normal entry works too |
| C | Limine panic naming a kernel or initramfs | That file is damaged | Reboot, pick a snapshot entry (snapshots carry their own kernels), then reinstall the kernel or restore |
| D | Our loader refused after a firmware update or reset | Firmware went back to factory keys | Secure Boot off; boot; clear keys again; `enable` (reuses the key files); on dual-boot suspend BitLocker first |
| E | Loader refused after restoring a snapshot from before `enable` | The restored root has no keys and no enroll line | Secure Boot off; boot; either stay off or clear keys and run `enable` again. Old keys are still in the kept backup root |
| F | "No bootable device" | Firmware lost our boot entry and the fallback is unsigned | Secure Boot off; the fallback boots; `sudo limine-install`; Secure Boot on |
| G | Nothing above works | ESP or root damaged | Secure Boot off; boot the ISO; the unlock-mount-chroot steps from risk R8; `limine-update` inside the chroot re-seals |

- Cases B and C look the same whether the cause is a bad update or an attacker. The README must say: if you did
  not just update or edit boot settings, do not type the passphrase; boot the ISO and look.
- The fallback is why B needs no USB stick. A signed sealed fallback would make F seamless and send B to the
  three-hour path. I chose B because Omarchy's tracker shows it happening.
- Keys in `@` make E loud and closed. Keys in their own subvolume would make E quiet and open: the restored
  system would sign an unsealed loader, which boots anything. Decision D5.
- Debugging boots that used the Limine editor now use the same route: Secure Boot off, fallback entry, editor.

## 8. Verification in QEMU

All of it runs on the build machine. No ISO rebuild is needed until `packages.target` changes.

**Harness.** One option on the VM class, not a fifth entry script:

- Firmware: nixpkgs `OVMFFull.fd` (Secure Boot build, SMM required) in place of `OVMF.fd`, for this option
  only. QEMU needs `-machine q35,smm=on` and `-global driver=cfi.pflash01,property=secure,value=on`. A blank
  variable store is Setup Mode, which is the state `enable` wants. *Verify* the attribute name, and whether S3
  suspend works on this firmware; if not, the suspend stage stays on the old firmware.
- Attach `swtpm` (move the helper out of `scripts/alongside_windows.py`), so Limine's forced measured boot runs
  as it will on real machines.
- Start from a `verify-fast` fixture copy, so the loop is about a minute per try.

**Stage `secure_boot`, positive:**

1. `status` exits 1 on the fresh fixture.
2. `enable`, reboot: `status` exits 0, `SecureBoot` reads 1, `SetupMode` reads 0.
3. Reinstall `linux` from the offline repo, reboot, `status` 0.
4. Reinstall `limine` alone: `status` 0 before the reboot and after it (Omarchy #10945).
5. Install any package (snap-pac snapshots), boot the newest snapshot entry, run `limine-snapper-restore`,
   reboot, `status` 0.
6. Alongside Debian fixture: after `enable`, the old-system entry starts Debian through its Microsoft-signed
   shim.

**Negative, each on a throwaway disk overlay and variable-store copy.** Pass means the guest never reaches the
login screen and the screenshot shows the expected text:

1. Append one byte to the initramfs on the ESP: Limine panic.
2. Append a comment to `limine.conf`: `CHECKSUM MISMATCH`.
3. Copy the stock loader over `limine_x64.efi`: firmware refuses it.
4. Boot `EFI/BOOT/BOOTX64.EFI` from the firmware menu with Secure Boot on: refused.
5. Pick the "EFI fallback" menu entry that `limine-install` adds: refused, no bypass.

**Recovery, once.** From negative 2: Secure Boot off, fallback entry, `limine-update`, Secure Boot on, `status`
0. Scripted if the variable store can be edited from the host (*verify* `virt-fw-vars`); otherwise done by hand
in the OVMF menu and written into the README, like risk R8.

**Later:** a hibernate run on this firmware; the Windows fixture with BitLocker off, then on with a suspend
around `enable` and a Limine reinstall afterwards.

**Keep it running.** Add the positive steps and negatives 1–3 to the weekly refresh run. Limine changed this
behavior in 11.2.0, 11.2.1 and 12, and both tools reach machines without passing this repo (risk R3).

QEMU cannot show vendor firmware behavior (Setup Mode menus, key writes that do nothing, boot-entry loss),
BitLocker on real TPMs, or FACEIT.

## 9. What stays manual

- **Firmware visits:** clearing keys to reach Setup Mode (prefer the option that deletes only the PK, which
  keeps `dbx`), switching Secure Boot on afterwards if the firmware does not, and every "Secure Boot off" in
  section 7. The README can only describe these generically.
- **Real machines are human steps.** `AGENTS.md` forbids agents from touching firmware. Order: VM, a spare
  laptop, the maintainer's laptop, the desktop last. Add them to `human-steps.md`.
- **MSI boards** (the maintainer's desktop is one): check that "Image Execution Policy" is not "Always Execute", or Secure Boot
  enforces nothing. If `enable` reports that the keys did not take, enroll them through the firmware's key
  management screen from files (`sbctl enroll-keys --export auth`), or leave Secure Boot off.
- **Windows:** suspend BitLocker before the firmware visit, resume after, keep the recovery key at hand.
- **FACEIT:** confirm on the maintainer's desktop that it accepts the result.
- **`dbx`:** if the firmware cleared it, it refills from Windows Update on a dual-boot PC, or from
  `fwupdmgr update` after installing fwupd. Not automated.
- **Firmware password and USB-boot lock,** if the user wants the stronger posture.

## 10. Phases

| Phase | Content | Size | Check |
|---|---|---|---|
| 0 | `efi_boot_entry` for the alongside entry. Independent of the rest | about 40 lines with tests | alongside verify, Debian fixture |
| 1 | Spike, no product code: Secure Boot firmware and swtpm in the harness; do `enable`'s steps by hand in a kept VM; answer section 13. Stop here if Limine's sealing misbehaves | about 60 harness lines | the positive and negative lists by hand |
| 2 | `sbctl` in `packages.target`, the command, the `zzz-` hook, the `ENABLE_VERIFICATION` pin, unit tests, stage `secure_boot` | about 150 + 10 + 80 + 150 lines | kept VM, then one ISO build and full verify |
| 3 | Installer wording, README sections, the "only Limine tools write the ESP" rule in `AGENTS.md` | about 50 lines | lint, tests, full verify |
| 4 | Human: spare laptop, laptop, the desktop with the FACEIT check | — | `status` on each |
| Later, own issues | Windows fixture runs; install next to BitLocker; an installer question that calls `enable`; TPM-bound unlock; a shim front end if Omarchy publishes one | | |

Phases 1 and 2 each go to Opus and Sol for attack before the next starts.

## 11. Decisions for the user

Needs an answer before phase 1:

- **D1. Start now or wait for Omarchy?**
  (a) Start now with own keys (recommended): their plan depends on a Microsoft-signed shim with no date, and
  the sealed-Limine half carries over if they ship one. (b) Keep waiting.
- **D3. Where are keys enrolled?**
  (a) A command on the installed system (recommended). (b) An installer question when the firmware is in Setup
  Mode. (c) Automatically in Setup Mode.
- **D6. BitLocker.**
  (a) `efi_boot_entry` now, keep refusing BitLocker at install, support it later as its own issue
  (recommended). (b) Include install-next-to-BitLocker in this work, as decided on 2026-11-07.
- **D7. The claim.**
  (a) Accept the narrower claim in section 6 and record TPM-bound unlock as the follow-up (recommended).
  (b) Enroll without Microsoft's certificates on single-boot laptops with integrated graphics for a stronger
  claim, as an extra mode. (c) Add TPM-bound unlock to this work.

Defaults I took; say so to change them:

- **D2. Trust anchor:** own keys with `-m` (recommended; shim was already dropped on 2026-11-07).
- **D4. Fallback loader:** (a) stock and unsigned, as the rescue path (recommended). (b) A signed sealed copy.
  (c) None.
- **D5. Key location:** (a) inside `@` (recommended; a pre-enable restore fails loudly). (b) Own subvolume
  (keys survive any restore, but that restore silently runs unsealed).
- **D8. Warn when a machine that had Secure Boot on now boots with it off:** (a) not in v1 (recommended; a
  proper answer comes with the TPM work). (b) A login notification from a boot-time check.

## 12. Dismissed

- **Shim and MOK now:** no Arch shim; borrowing another distro's ties us to their revocations; our own needs an
  EV certificate and Microsoft review.
- **UKIs:** more signed files, a known MSI failure, broken snapshot dedup, and a second change to boot policy.
- **A signed ISO:** needs the shim.
- **Installer-time or automatic enrollment:** section 4.
- **sbctl's file database (`sbctl sign -s`) and `zz-sbctl.hook` as the signer:** a second signer for one file;
  it missed the loader in Omarchy #10945. The Limine post-hook signs every time.
- **TPM-backed sbctl keys (`--keytype tpm`):** root can still sign, and it adds TPM failure modes.
- **A `disable` subcommand, key rotation, a key backup:** nothing needs them; rotation is "clear keys, delete
  `/var/lib/sbctl`, enable".
- **Turning off the reset pre-hook to shrink the unsigned window:** it trades case A for case B.
- **`enroll-keys -f` (firmware built-in certificates) by default:** widens trust to the vendor's keys; use by
  hand on a board whose option ROMs need it.
- **Module signature enforcement, lockdown:** 5.7.
- **A separate subvolume for keys:** D5.

Forward path: if a Microsoft-signed shim becomes available, a second mode can put it in front of the same
sealed loader (add an SBAT section, sign with a MOK key, enroll through MokManager). That removes the firmware
visit. Sections 5.4 to 5.7 stay as they are.

## 13. To verify in phase 1, and where to attack

| # | Question | Affects |
|---|---|---|
| V1 | Does a sealed loader still panic on a config mismatch with Secure Boot off? (`limine enroll-config --reset` says "will not check config integrity", which suggests yes) | wording of case B |
| V2 | Is the enrolled hash stored as ASCII hex after `++CONFIG_B2SUM_SIGNATURE++`? (the marker is in the binary) | status check 5 |
| V3 | In `sbctl enroll-keys`, is the PK written last, and what does a half-finished run leave? | `enable` step 7 |
| V4 | With sbctl installed and no keys, is `zz-sbctl.hook` silent and successful on every kernel update? | every machine gets sbctl |
| V5 | Does `enroll-keys -m` work offline, and which Microsoft certificates land in `db`? | offline rule, Windows boot |
| V6 | After the PK is enrolled, does OVMF enforce on the next boot without a manual switch? Real firmware varies | step 10 text |
| V7 | `OVMFFull`: blank store is Setup Mode; S3 works or not | harness |
| V8 | Can the variable store be switched to Secure Boot off from the host? | scripted recovery test |
| V9 | Measured boot with a TPM attached: no panic, no visible delay | real machines |
| V10 | Booting a snapshot entry and restoring it under sealing; the restored root's keys sign | snapshots |
| V11 | `efi_boot_entry` on OVMF, and what Limine does when the description is missing | 5.6 |
| V12 | Nothing ever signs `BOOTX64.EFI`; the "EFI fallback" menu entry is refused | D4 |
| V13 | Length of the unsigned window for `limine-mkinitcpio` and for `limine-snapper-sync` | case A likelihood |
| V14 | FACEIT with a custom PK; BitLocker suspend and resume across enrollment | the dual-boot desktop, by hand |

Where I expect the design to be weakest:

1. Everything rests on Limine's hash enforcement, in a loader whose Secure Boot behavior changed three times.
2. The recovery story is "switch Secure Boot off", which is also what an attacker wants the user to do.
3. Root holds the signing key, and the `zzz-` hook re-seals whatever `limine.conf` root left behind.
4. A command the user must remember to run, against an installer that could have asked.
5. Vendor firmware: MSI key writes, boot-entry loss, firmware updates that restore factory keys.

## Log

Ran: `gh issue view 49`; read the Limine docs and scripts installed on the laptop; `zgrep` on the running
kernel's config; `pacman -Si`; `gh search code` and `gh issue view` on omacom/omarchy, omarchy-iso and
omarchy-pkgs; fetched Omarchy's plan file, one migration, the configurator and the sbctl 0.18 man page.
Not run: any VM, sbctl, or anything under an enforcing firmware. Delegated: nothing; each lookup was one
command. Fetched files are in the session scratchpad and were treated as data.

Status: complete.
