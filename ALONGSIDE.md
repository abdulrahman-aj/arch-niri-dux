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
kernel's new partition geometry before formatting. It suppresses the pinned
Limine package's initial EFI deployment hook until configuration completes,
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

## Parent verification

Run sequentially from this checkout:

```
make test
./scripts/build-in-dev python3 -m unittest discover -s tests -p test_alongside.py
# Parent only, after integrating other approved installer changes:
make iso
./scripts/build-in-dev python3 scripts/verify-alongside.py --iso /absolute/new.iso --fixture /absolute/debian.qcow2 --firmware /absolute/debian-OVMF_VARS.fd --directory /absolute/fresh-linux-evidence
```

`verify-alongside.py` is an unexecuted acceptance harness, not proof of VM
acceptance. It requires the same KVM/render-node access as `make verify`. Its
fixture contract is a real Debian amd64 UEFI installation with a serial ttyS0
login, `tester` / `vm-verification-only`, passwordless sudo, a mounted `/boot/efi`,
a registered `EFI/debian/{shim,grub}x64.efi` entry, and
`/var/lib/alongside-sentinel` containing `preserve-existing-linux`. Provision
from official Debian minimal installer or an official Debian cloud image; disable
cloud-init growpart/resizefs, then enlarge the *virtual disk* offline to leave
one >=32 GiB extent. Keep its matching OVMF variables. No EFI stub qualifies.
Official source: https://cloud.debian.org/images/cloud/ .
The runner uses a disposable backing overlay and copies firmware variables. It
boots real Debian first, hashes all original partitions while offline, installs,
compares hashes, boots new Arch by default, then selects Debian through Limine
and checks the sentinel. The menu timing/End-key selection still needs VM
validation. A failing/incomplete run never writes SUCCESS.

Windows acceptance remains unimplemented/unexecuted: acquire official Windows
11 Enterprise evaluation media from
https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise .
A real installed Windows 11 GPT/UEFI fixture (no EFI stub), authenticated guest
command transport, pre/post boot checks, original-partition hashing while
Windows is shut down, and Limine-selected Windows boot are required before this
TODO can be completed. No download was attempted: large fixture execution is
reserved for the parent to serialize with builds, not blocked on a claimed
Microsoft interaction. Linux fixture provisioning also remains parent work;
the runner does not create a real installation itself.

Keep at least 8 GiB host headroom, monitor `df -h .`, reuse the existing ISO/cache,
and run Linux then Windows sequentially. The Linux runner requires 20 GiB free
before starting; monitor growth during execution too. Never collect broad Nix
GC or delete unrelated user files. Do not treat unit tests, partition hashes,
or a boot-menu screenshot as a substitute for real OS boot acceptance. Keep
TODO.md listed until independent review and both real OS scenarios pass.
