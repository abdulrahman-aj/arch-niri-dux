# Alongside installation (draft; VM acceptance pending)

This path supports x86-64 UEFI, a valid GPT with the standard 128-entry array,
512/4096-byte logical sectors, an unused writable disk, and one contiguous
already-unallocated region of at least 32 GiB. Auto swap can require more space.
Select the disk, full aligned free extent and an existing firmware boot entry.
The final summary lists the exact partition numbers, inclusive sectors, byte
counts and types. Automation uses `mode: "alongside"`, `free_region: [first,last]`
and `existing_boot: "0001"` in addition to the normal private JSON settings;
`--confirm-disk` must still match exactly. Sector numbers use the disk's logical
sector size. Never use these examples on a physical disk for verification.

The installer creates a dedicated 4 GiB ESP and encrypted Btrfs partition using
unused GPT slots. Existing partitions are never shrunk, moved or formatted.
Existing ESPs are mounted read-only only to validate the selected x86-64 PE EFI
application. Their size does not matter and their fallback files stay intact.
The old OS entry chainloads the original EFI path by its original GPT partition
GUID, retaining adjacent GRUB/shim/BCD resources on the original ESP.

The installer compares disk identity, both GPT copies (including unused slots),
protective MBR, free extents and firmware entries/order immediately before
partition creation. It verifies the saved partition entries afterward and the
kernel's new partition geometry before formatting. It uses Limine's documented `SKIP_UEFI` setting for every initial package and
kernel hook until configuration completes,
registers with `efibootmgr --create-only`, and prepends the new entry while
retaining existing entries and order. Erase mode retains its prior wipe path.

Unsupported: BIOS/MBR, hybrid MBR, damaged/relocated GPT metadata, nonstandard
GPT entry counts, mounted/active disks, bootloaders without a matching registered
FAT ESP entry on this disk, EFI paths containing spaces/non-ASCII characters,
and free space spread across smaller extents. There is no partition resizing,
ESP expansion/reuse, BitLocker unlocking, Secure Boot work, RAID/LDM migration or
firmware repair. TPM-bound Windows installations can request recovery after a
boot-chain change: the Windows acceptance fixture must have BitLocker/device
encryption disabled. Detected BitLocker/device encryption and Windows dynamic disks are rejected
before mutation. This draft is not ready for physical installation.

An external partition editor must not run during installation. Revalidation
closes the long confirmation/package-preflight window; it cannot make GPT edits
transactional against an uncooperative concurrent writer or power failure.
Failures after partition creation leave the new partitions in place, with the
original OS untouched; no rollback deletes partitions automatically.

## Automated provisioning and acceptance

All commands operate only on disposable QEMU disks. Run scenarios sequentially.
The normal Nix dev shell provides QEMU, OVMF, xorriso and swtpm. `fetch-alongside`
checks host headroom, uses official sources and records resolved URLs and SHA256.
Debian also verifies the publisher's SHA512 manifest. Microsoft's LTSC evaluation
ISO is fetched directly from the evaluation center's official download link;
its local SHA256 is recorded. No product key or personal credentials are needed.

```
make test
make iso
make fetch-alongside ARGS="--os linux --directory out/alongside/media"
make fetch-alongside ARGS="--os windows --directory out/alongside/media"
make provision-alongside ARGS="--os linux --media out/alongside/media/debian.qcow2 --iso out/arch-niri-dux-2026.11.02-x86_64.iso --directory out/alongside/linux-fixture"
make verify-alongside ARGS="--os linux --iso out/arch-niri-dux-2026.11.02-x86_64.iso --fixture out/alongside/linux-fixture/disk.qcow2 --firmware out/alongside/linux-fixture/OVMF_VARS.fd --directory out/alongside/linux-test"
make provision-alongside ARGS="--os windows --media out/alongside/media/windows11-ltsc-eval.iso --iso out/arch-niri-dux-2026.11.02-x86_64.iso --directory out/alongside/windows"
make verify-alongside ARGS="--os windows --iso out/arch-niri-dux-2026.11.02-x86_64.iso --fixture out/alongside/windows/disk.qcow2 --firmware out/alongside/windows/OVMF_VARS.fd --windows-token out/alongside/windows/report-token --directory out/alongside/windows-test"
```

Use the actual generated ISO filename and fresh evidence directories. Media
already downloaded can be checked/recorded using `--verify-existing` with the
fetch command. Do not download it again merely to run a second scenario.

Linux provisioning uses the official Debian 13 cloud installation, configures
only a disposable test account/sentinel through the live ISO, enlarges the
virtual disk without growing any existing partition, relocates the backup GPT
as fixture preparation, and registers the original signed Debian shim. Debian
boots its real kernel and userspace; no EFI stub substitutes for an installed OS.

Windows provisioning uses Windows 11 Enterprise LTSC evaluation 24H2, a sparse
64 GiB virtual SATA disk, a 100 MiB FAT ESP, 16 MiB MSR and 24 GiB NTFS system
partition, leaving the rest unallocated. Microsoft WinPE reads
an unattended answer file from a seed DVD and deploys the official image using
DISM `/Apply-Image /Compact` and BCDBoot, with an emulated TPM2. The standard
Setup hardware eligibility screen rejected this VM; WinPE deployment changes
no Secure Boot state or firmware keys. Device
encryption and hibernation are disabled inside this disposable fixture. A local
test account and boot task report the running Windows product, build and sentinel
over QEMU user networking to a random-token route on localhost:18769;
no host listener is exposed on external interfaces. Windows then shuts down.
The reporter rejects old builds, missing sentinels and invalid routes.

Acceptance uses a backing overlay and a copy of the fixture's firmware. It
boots the original OS first, hashes every byte of every original partition
while offline, installs into the selected region, compares those hashes, boots
the new encrypted Arch installation by default, reinstalls the pinned kernel
package to exercise actual package/snapshot hooks, verifies the existing OS
entry survives, reboots updated Arch, and chainloads the original OS by its
Limine entry path. Selecting a path explicitly avoids timing-dependent UI keys.
Only test instrumentation adds a serial console to the new Arch installation.

Evidence includes input hashes, serial/QEMU logs, original partition hashes,
firmware listings, menu configuration, Windows guest reports and a milestone
list. Only a full run writes `SUCCESS`. Never treat a screenshot or a loader
stub as proof of a successful old-OS boot. Real-OS acceptance results are still
pending and must be recorded before this draft is considered ready.

Keep at least 8 GiB host headroom. Guest waits and Windows report waits stop at a 9 GiB threshold to protect
that reserve; media downloads reserve it before writing. Monitor
`df -h .` as concurrent root work can consume space. After preserving evidence,
remove only this task's disposable failed/finished VM overlays or downloaded
Windows ISO when no longer needed. Retain input hashes, logs and reports. Do not
run broad Nix GC or delete unrelated user/cache files. Keep TODO.md listed until
independent review and both real OS scenarios pass.

Interrupted Windows provisioning can resume without recreating its disk using
`make provision-alongside ARGS="--os windows --finish-windows --iso /absolute/new.iso --directory /absolute/windows-fixture"`. This requires the saved successful Windows OS report and verifies the qcow2 before firmware registration. The TPM emulator restarts for each QEMU run with its original state preserved.
