# NVIDIA support

**Status (2026-11-12):** agreed, not started. Build in three PRs (section 10).

**Decisions (user, 2026-11-11/12):**
- Follow Omarchy's approach.
- A separate `packages.nvidia` list on the ISO, not in the baseline metapackage. Turing and newer: `nvidia-open`,
  `nvidia-utils`, `libva-nvidia-driver`, `lib32-nvidia-utils` (multilib enabled).
- **D1:** Maxwell, Pascal and Volta (GTX 900/1000): **pin Omarchy's signed `nvidia-580xx-*` builds**, like the Limine
  pins. Building them ourselves stays possible later (section 9). Related: #113.
- **D2:** a package-owned policy file that `apply-nvidia` only links, re-run by an ALPM hook on the driver packages.
- **D3:** a wizard choice when a supported card is found: NVIDIA driver (default) or Nouveau.
- Kepler and older: Nouveau, with a message.
- Real-hardware test: a GTX 1070 (580xx path) before calling it supported.

The rest of this file is the implementation spec (Fable 5.2, 2026-11-12). Where it says "recommendation", the
decisions above settle it.

---


Status: COMPLETE. Spec only; no code changed. Based on main @ 6cc9938, archive date 2026/11/10.

## 0. Three calls the architect must confirm first

Reading Omarchy's source and our release code turned up three places where I recommend something other
than the literal decision. Everything else follows the decisions as given.

| # | Decision as given | Recommendation | Why |
|---|---|---|---|
| D1 | Vendor `nvidia-580xx-dkms` as a `packages/aur` recipe, built and signed by us | **Pin Omarchy's signed builds** from `pkgs.omarchy.org`, the same way we already pin `limine-mkinitcpio-hook` | The report said 580xx is "AUR-only, no signed source". It is wrong: Omarchy's stable repo carries `nvidia-580xx-dkms`, `nvidia-580xx-utils` and `lib32-nvidia-580xx-utils`, signed by the key we already ship and trust. Pinning costs about 15 lines. Vendoring costs 350–500 lines: split-package support, a 64 MiB release cap to lift, 430 MB on a 1 GB Pages site, and 800 MB of downloads in every PR's CI run. Package names are the same either way, so we can vendor later without touching the installer. Section 9 specifies the vendored path if the user still wants it. |
| D2 | `apply-nvidia` writes config; `post_upgrade` re-runs it behind a state guard | `apply-nvidia` only links one **package-owned** policy file; an ALPM hook on the driver packages re-runs it | Omarchy keeps the hybrid/`kms` rule in a static package file that is evaluated at every initramfs build. Copying that leaves no per-machine content to regenerate. Policy changes arrive with the package, and our boot-policy hook already rebuilds the initramfs on every `arch-niri-dux` upgrade. A `post_upgrade` scriptlet would also run mid-transaction, before DKMS and before the new kernel is in place. |
| D3 | Experimental label | Put the label in a **wizard choice** shown only when a supported card is found: NVIDIA driver (default) or Nouveau | The opt-out is the escape if the driver path breaks on a machine we never tested. About 10 lines using the existing `choose()`. |

## 1. Design in one page

```
packages/packages.nvidia         nvidia-open nvidia-utils lib32-nvidia-utils libva-nvidia-driver
packages/packages.nvidia-580xx   nvidia-580xx-dkms nvidia-580xx-utils lib32-nvidia-580xx-utils linux-headers
        │  both resolve with packages.target in one pacman transaction at the archive date (+ multilib),
        │  580xx's three packages come from the Omarchy pin; neither list is in the baseline metapackage
        ▼
ISO repo/ ──► installer: sysfs detect ──► band ──► list added to the first pacstrap
                                                        │
arch-niri-dux package (static, inert without the driver):
  /usr/share/arch-niri-dux/mkinitcpio-nvidia.conf   MODULES+=(nvidia…) and the kms rule (hybrid keeps kms)
  /usr/lib/modprobe.d/arch-niri-dux-nvidia.conf     options nvidia_drm modeset=1 fbdev=1
  /etc/nvidia/nvidia-application-profiles-rc.d/50-arch-niri-dux-niri.json   Niri VRAM profile
  /usr/lib/arch-niri-dux/apply-nvidia               links or unlinks /etc/mkinitcpio.conf.d/nvidia.conf
  60-arch-niri-dux-nvidia.hook                      runs apply-nvidia when a driver package changes
```

Bands (Omarchy's thresholds, PCI vendor `0x10de`, class `0x03*`): device `>= 0x1e00` → `open`;
`0x1340 <= device < 0x1e00` → `580xx`; lower → Nouveau and a message. No NVIDIA card → nothing.

Verified today against the pinned date: both lists resolve together with `packages.target` and
`packages.live` in one transaction once `multilib` is added. `nvidia-open` 615.78.08-6 provides
`NVIDIA-MODULE` and matches `linux` 7.2.15.arch1-1. Omarchy's 580.178.04 DKMS source is paired with their
7.2.15 kernel, the same version as ours.

## 2. Omarchy comparison

| Decision | Omarchy | Ours | Verdict |
|---|---|---|---|
| Detection | sysfs IDs, not `lspci` (does not wake a suspended dGPU); same thresholds | same, in `install.py` | Match |
| Turing+ packages | `linux-omarchy-nvidia-open nvidia-utils lib32-nvidia-utils libva-nvidia-driver` | Arch `nvidia-open` + the same three | Match (their kernel, our stock kernel) |
| 580xx packages | `nvidia-580xx-dkms nvidia-580xx-utils lib32-nvidia-580xx-utils`, no libva driver | same | Match |
| 580xx source | AUR recipe vendored in `omarchy-pkgs`, built with `zstd -22`, signed, published | D1: consume that build. Section 9: do what they do | Differs under D1, cheaper |
| Kepler and older | wiki link, exit 0 | message, Nouveau | Match |
| Offline | offline mirror from `omarchy-other.packages` | ISO `repo/` from two hardware lists | Match |
| Headers, toolchain | `base-devel` + headers on every machine | `base-devel` everywhere (already), `linux-headers` only on 580xx | Differs: smaller default install |
| `modeset=1` | installer writes `/etc/modprobe.d/nvidia.conf` | package-owned `/usr/lib/modprobe.d` file, plus `fbdev=1` | Differs: project rule prefers package files |
| Early KMS | installer writes `/etc/mkinitcpio.conf.d/nvidia.conf` | same path and name, as a link to a package file | Match in effect |
| Hybrid keeps `kms` | static `omarchy_hooks.conf`, evaluated per build | same logic, in the linked file next to `MODULES` | Match |
| Re-run on upgrade | none automatic; migrations and a manual `omarchy-apply-hardware` | hook on driver packages | Differs: we avoid migrations |
| Session env vars | Hyprland-only, gated on `boot_vga` | none; Niri needs none | Differs deliberately. Revisit `LIBVA_DRIVER_NAME` after hardware tests |
| VRAM profile | none (the 580xx recipe ships one that names `niri`) | our own Niri profile | Differs: Niri wiki requires it on Turing+ |
| Hybrid tooling | `supergfxctl` toggle, `force-igpu` sleep hook | none in v1 | Differs: ASUS-specific, out of scope |
| Suspend services | none (580xx package enables its own) | none | Match |
| Asking the user | installs silently | D3 choice while experimental | Differs |
| multilib | on for x86_64 (inferred from `nvidia.sh`; their pacman.conf not read) | on | Match |
| Secure Boot | nothing | section 8 | n/a |

## 3. PR 1: lists, multilib, pin (about 100 lines)

Files:

- **`packages/packages.nvidia`**, **`packages/packages.nvidia-580xx`**: contents as in section 1, one name
  per line.
- **`scripts/package_lock.py`**
  - `ARCH_REPOS = ('core', 'extra', 'multilib')`.
  - `requested_packages()`: read every `packages/packages.*` plus `archiso/packages.live` instead of two
    hard-coded files. A future hardware list then needs no code.
  - `OMARCHY_NAMES`: add the three 580xx names; fix the "only the two reviewed packages" message.
  - `omarchy_arch_deps()`: strip version constraints before subtracting pin names
    (`nvidia-580xx-dkms` depends on `nvidia-580xx-utils=580.178.04`, which must not reach Arch resolution).
- **`packages/omarchy-pin.json`**: regenerate with `make lock ARGS='--date 2026/11/10'` on the build machine. That
  keeps the archive date and only extends the pin. Expect five entries.
- **`scripts/build-guest`**: copy `packages/packages.*` into the live image's `SHARE`, not only
  `packages.target`.
- **`desktop/etc/pacman.d/arch-niri-dux.conf`**: add `[multilib]` after `[extra]`. Without it,
  `lib32-nvidia-utils` pins `nvidia-utils` to its old version and blocks every later `pacman -Syu`.
- **`keys/trusted-packagers.gpg`**: the build names any missing signer (heftig, arojas, gromit and ptr1337
  sign the new packages). Follow `keys/README.md` and list added keys in the PR.
- **`packages/README.md`**: hardware lists, multilib, five Omarchy packages.

Not changed: `packages.target`, the baseline metapackage, `iso_payload.py` (it derives from
`requested_packages` and the pin), release scripts.

Tests: `requested_packages` picks up a new list file; `omarchy_arch_deps` drops versioned self-references;
update the `test_iso_payload` fixture. Checks: `make lint test`, then `make iso-development` on the build machine and
confirm the new packages in `out/packages.manifest.json`. PR body needs an `Existing installs:` line
(multilib reaches existing installs whose config file is unmodified).

## 4. PR 2: package policy (about 150 lines)

- **`desktop/usr/share/arch-niri-dux/mkinitcpio-nvidia.conf`**
  ```bash
  # Early KMS for the NVIDIA driver. apply-nvidia links this file as
  # /etc/mkinitcpio.conf.d/nvidia.conf, which sorts after arch-niri-dux.conf.
  MODULES+=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)
  # The driver does its own modesetting, so kms only adds nouveau and about
  # 100 MB of firmware for a driver nvidia-utils blacklists. Drop kms only when
  # every display controller is NVIDIA. A hybrid machine's iGPU needs it for the
  # unlock prompt; so does an unreadable PCI tree or a machine without its card.
  <loop over "${ARCH_NIRI_DUX_PCI_DEVICES:-/sys/bus/pci/devices}"/*, as in Omarchy's omarchy_hooks.conf;
   unset the temporaries>
  ```
  The name matters. mkinitcpio concatenates drop-ins in `sort -V` order and `arch-niri-dux.conf` assigns
  `MODULES=(btrfs)`. The report's `10-arch-niri-dux-nvidia.conf` sorts first and would be silently
  overwritten. `nvidia.conf` sorts after.
- **`desktop/usr/lib/arch-niri-dux/apply-nvidia`** (sh, mode 0755 via the PKGBUILD `chmod` line)
  ```sh
  policy=/usr/share/arch-niri-dux/mkinitcpio-nvidia.conf
  link=${ARCH_NIRI_DUX_ROOT:-}/etc/mkinitcpio.conf.d/nvidia.conf
  if pacman -T NVIDIA-MODULE >/dev/null 2>&1; then
      # A regular file here is the user's own configuration.
      if [ -L "$link" ] || [ ! -e "$link" ]; then ln -sfn "$policy" "$link"; fi
  elif [ "$(readlink "$link" 2>/dev/null)" = "$policy" ]; then
      rm "$link"
  fi
  ```
  It never rebuilds the initramfs. The installer rebuilds later in `configure-target.sh`; in a pacman
  transaction limine's `90-mkinitcpio-install.hook` does (it triggers on `extramodules/` and `dkms.conf`).
- **`desktop/usr/share/libalpm/hooks/60-arch-niri-dux-nvidia.hook`**: `Type = Path`, operations Install,
  Upgrade, Remove, targets `usr/lib/modules/*/extramodules/nvidia*` and `usr/src/nvidia-*/dkms.conf`,
  `When = PostTransaction`, `Exec = /usr/lib/arch-niri-dux/apply-nvidia`. The number must stay below
  DKMS (70) and mkinitcpio (90).
- **`desktop/usr/lib/modprobe.d/arch-niri-dux-nvidia.conf`**: `options nvidia_drm modeset=1 fbdev=1`.
- **`desktop/etc/nvidia/nvidia-application-profiles-rc.d/50-arch-niri-dux-niri.json`**: the Niri wiki
  profile (`procname` `niri` → `GLVidHeapReuseRatio` 0). Not in `backup=`.
- **`desktop/README.md`**: an NVIDIA section, including the supported way to add the driver later
  (`sudo pacman -S` the list; the hook does the rest).

Tests (`tests/test_nvidia_policy.py`), behavior only:
- Source the policy file in bash over fake PCI trees and read back `MODULES`/`HOOKS`: NVIDIA-only drops
  `kms`; Intel + NVIDIA keeps it; no NVIDIA device keeps it; unreadable entry keeps it; modules appended
  in all cases.
- `apply-nvidia` with a fake `pacman` on `PATH` and `ARCH_NIRI_DUX_ROOT`: links when the driver is
  present, is idempotent, removes only its own link, leaves a user's regular file alone.

Checks: `make test`, `make verify-fast` to a kept VM. PR body: `Existing installs:` a hand-installed
driver gains early KMS on its next driver upgrade unless `/etc/mkinitcpio.conf.d/nvidia.conf` already exists.

## 5. PR 3: installer and verification (about 250 lines)

**`installer/install.py`**
- `read_package_list(path)`: extract the existing parse-and-validate block from `install()` first, then
  use it for all lists.
- `detect_nvidia(devices=Path('/sys/bus/pci/devices'))` → `('open' | '580xx' | 'unsupported', 'vvvv:dddd')`
  or `None`. Prefer `open` when several NVIDIA devices exist. Skip unreadable entries.
- `Settings.nvidia: str = 'auto'`, validated against `auto`, `open`, `580xx`, `none`. `--config` JSON can
  force a band; that is how VMs exercise the paths.
- `nvidia_packages(choice, devices, share)` → `(band, packages)`, a pure function that tests call.
- `wizard()`: when a supported card is found, `choose()` between "NVIDIA driver (experimental)" and
  "Open-source Nouveau driver". On `unsupported`, print: card is too old for NVIDIA's current drivers,
  installing with Nouveau, Arch wiki link.
- `install()`:
  1. Add the band's packages to the preflight `pacman --print` resolve, so presence and signatures are
     checked before the disk is touched.
  2. Add them to the first `pacstrap`. On `580xx`, print that building the driver takes a few minutes.
  3. After that `pacstrap`: run `apply-nvidia` in the chroot, then require `modinfo -k <kver> nvidia_drm`
     to succeed for every kernel under `/usr/lib/modules`. A failed DKMS hook does not fail pacman, so
     this check is the only thing that stops a broken install. On failure, raise with advice to reinstall
     and pick Nouveau.
  4. No change to `configure-target.sh`: its `limine-mkinitcpio` now builds with the link in place.
  5. Completion message gains one line when the driver was installed: NVIDIA support is experimental.

**Verification harness**
- `scripts/verify.py --nvidia {open,580xx}` writes the field into the install config.
- One probe (in `scripts/verification/policy.py`): default run asserts no `NVIDIA-MODULE` and no link;
  forced run asserts the band's packages, `modinfo nvidia_drm` for the running kernel, the link resolves,
  the default initramfs contains `nvidia_drm` and still contains `virtio-gpu`, and `dkms status` reports
  installed on `580xx`.
- Forced runs log `NVRM: No NVIDIA GPU found`; allow that line if any stage scans for errors.

**Docs**: `installer/README.md`, `README.md` hardware note with the experimental label,
`VERIFICATION.md` (forced runs, hardware gap, the checklist in section 7).

Tests (`tests/test_installer.py`): band boundaries over fake sysfs (`0x1e00`, `0x1dff`, `0x1340`,
`0x133f`), NVIDIA audio function ignored, hybrid, no card; `none` and forced bands through
`nvidia_packages`; a malformed NVIDIA list stops before disk mutation; invalid `Settings.nvidia` rejected.

Checks: `make lint test`; `make iso-development`; three full `make verify` runs on the build machine, one at a time:
default, `ARGS='--nvidia 580xx'`, `ARGS='--nvidia open'` (about an hour in total).

## 6. What VMs prove, and what they cannot

Proven without NVIDIA hardware: both lists install offline from the ISO with signatures checked; the
single-transaction resolve at the pinned date; DKMS compiles 580xx against the pinned kernel (compiling
needs no GPU); `nvidia-open` matches the pinned kernel; the initramfs builds with the four modules; the
`kms` rule in all four hardware shapes (unit tests); a machine with the driver but no card still boots to
the greeter with graphical unlock, which is also the "card removed" case; the hook links and unlinks; the
default install is unchanged.

Not provable: the module binding to a card, modeset and fbdev, Plymouth on NVIDIA outputs, VRAM profile
effect, video decode, suspend, hibernate, hybrid behavior, the live ISO's Nouveau on real cards.

## 7. Real-hardware checklist (brother's GTX 1070, `10de:1b81`, 580xx path)

Before: Secure Boot off, data backed up, ISO from a build that passed section 5.

1. Boot the ISO. The wizard appears. If the screen stays black, press `e` on the boot entry, add
   `nomodeset`, and report it.
2. The wizard names the card and offers the NVIDIA driver as experimental. Accept. Note how long the
   package step takes.
3. First boot: graphical disk-unlock prompt at native resolution, then greeter, then desktop.
4. Run and save the output:
   ```sh
   { pacman -Q nvidia-580xx-dkms nvidia-580xx-utils lib32-nvidia-580xx-utils linux-headers
     dkms status
     cat /sys/module/nvidia_drm/parameters/modeset /sys/module/nvidia_drm/parameters/fbdev
     lsmod | grep -E 'nvidia|nouveau'
     readlink /etc/mkinitcpio.conf.d/nvidia.conf
     systemctl is-enabled nvidia-suspend nvidia-resume nvidia-hibernate
     nvidia-smi
     journalctl -b -p err --no-pager; } 2>&1 | tee ~/nvidia-report.txt
   ```
   Expect: DKMS "installed", `Y` twice, no `nouveau`, the link, three "enabled", `niri` near 100 MiB in
   `nvidia-smi` and not near 1 GiB.
5. Desktop: bar and window animations smooth; a 1080p video in Chromium plays; `chrome://gpu` reports
   hardware acceleration.
6. Suspend and resume three times. Display returns without corruption.
7. Hibernate once. The session returns after unlock. A failure here is a known risk; report it, do not
   retry repeatedly.
8. Plug and unplug a second monitor.
9. Online `sudo pacman -Syu`, reboot. If the kernel changed, the output shows a DKMS rebuild.
10. Boot a snapshot from the Limine menu, then boot normally again.

If graphics fail after install: at the Limine menu press `e`, append
`module_blacklist=nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm`, boot, switch to a text console and save
`journalctl -b -1`. If an update broke it, boot the previous snapshot.

The label comes off the 580xx path when steps 1–10 pass. **No machine in the fleet has a Turing+ card**
(msi-dux is AMD-only), so the `open` path stays experimental until someone with an RTX card runs the same
list.

## 8. Sizes, risks, Secure Boot

**ISO**: 2.74 GB today → about 3.8 GB (+1.06 GB, +39%). Turing+ list +560 MB (`nvidia-utils` 330,
`lib32-nvidia-utils` 121, lib32 dependencies about 95, `nvidia-open` 10). 580xx +425 MB from Omarchy
(290, 84, 51) plus `linux-headers` 72. The lib32 share is about 270 MB if size ever matters.
**Installed**: about 1.6 GB extra on NVIDIA machines, nothing elsewhere. **Install time**: a few minutes
of DKMS on 580xx. **Build**: about 1 GB more to download once, then cached.

Risks, most serious first:

1. **Kernel moves ahead of the 580xx driver.** Our machines take `linux` from rolling Arch and the driver
   from Omarchy stable, which is tested against Omarchy's own kernel. A new kernel series can fail the
   DKMS build, and Nouveau is blacklisted, so the next boot has no graphics. Recovery exists: `snap-pac`
   snapshots the system before the update and Limine boots it. Follow-up to queue, not start: a weekly CI
   job that compiles the pinned Omarchy DKMS source against Arch's current headers.
2. **Turing+ path has no tester.**
3. **Omarchy is the 580xx supplier.** They can rename or drop packages (`make lock` fails loudly), and
   stable trails NVIDIA's security releases (580.178.04 there, 580.196.07 in the AUR today).
4. **Hibernate with early KMS** is unverified on both paths. 580xx preserves video memory to `/var/tmp`.
5. **Hybrid laptops** get correct boot behavior and nothing else: no offload helper, no dGPU power policy.
6. **Live ISO on the newest cards** depends on Nouveau or the firmware framebuffer; `nomodeset` is the
   fallback.
7. **`linux-headers` is not in the baseline.** A user who removes it breaks the next DKMS build.
8. **multilib for everyone**, including existing installs with an unmodified config file.
9. **Keyring additions** for new packagers need the usual review.

**Secure Boot (design §5.7).** Nothing to sign: Arch's kernel loads unsigned and DKMS modules under Secure
Boot. Four notes for that design:
- Hook order is ours (60), DKMS (70), mkinitcpio (90). Whatever seals or hashes the initramfs must run
  after 90, on driver transactions too.
- The invariant "never add `module.sig_enforce=1` or `lockdown=`" now protects a shipped feature.
- Boot policy grows from three places to four: add `mkinitcpio-nvidia.conf` and its link to that list.
- The recovery advice in section 7 edits the kernel command line. Once the Limine editor is sealed, the
  snapshot entry is the only recovery path; update the checklist then.

## 9. If the user keeps "vendored in our repo" (D1 as decided)

Same installer, lists and policy. Replace the pin changes in PR 1 with two extra PRs (350–500 lines):

- **Recipes**: `packages/aur/nvidia-580xx-utils/` (11 files, AUR 580.196.07-1, record the AUR commit) and
  `packages/aur/lib32-nvidia-580xx-utils/`, hash-pinned in `aur-recipes.json`. Keep the upstream files
  unmodified; optionally add Omarchy's `COMPRESSZST` line (saves 89 MB).
- **Split packages**: the recipe builds `nvidia-580xx-utils`, `opencl-nvidia-580xx` and
  `nvidia-580xx-dkms`. Add an optional `"packages"` field per recipe; `dux_apps.recipes()` and `build()`
  return one entry per output; `requested_packages`, `iso_payload._manifest`,
  `integration_release.validate_manifest/fetch/publish` compare output names, not recipe names.
- **Dependencies**: list the Arch dependencies explicitly in `packages.nvidia-580xx` (`dkms libglvnd
  egl-wayland egl-wayland2 egl-gbm egl-x11 lib32-zlib lib32-gcc-libs lib32-libglvnd linux-headers`),
  because our recipes are outside the Arch resolve.
- **Release path**: raise the 64 MiB cap in `integration_release.download`; add `nvidia-580xx-*` and
  `lib32-nvidia-580xx-*` to the history patterns in `publish-integration` and to
  `keys/integration-history-start.json`; the Pages site grows by about 430 MB of its 1 GB limit.
- **CI and builds**: every PR run and every development ISO downloads two 400 MB NVIDIA installers and
  packages 1.2 GB. Add a `SRCDEST` cache or build only when the recipe hash changes.
- **Updates**: manual re-vendor from the AUR; `bump_dux_apps.py` does not cover it.
- Gain: we choose when to take a driver or kernel-compatibility fix (risk 1 and 3).

## 10. PR order

| PR | Content | Size | Gate |
|---|---|---|---|
| 1 | lists, multilib, Omarchy pin | ~100 | `make lint test`, `make iso-development` |
| 2 | policy file, `apply-nvidia`, hook, modprobe, profile | ~150 | `make test`, `make verify-fast` |
| 3 | installer, harness flag and probe, docs | ~250 | three full `make verify` runs |
| then | brother's checklist; label decision | — | section 7 |

PR 2 does not depend on PR 1. PR 3 needs both.

## Appendix: evidence

- Repo: `package_lock.py` (`ARCH_REPOS`, `requested_packages`, `omarchy_arch_deps`), `dux_apps.py` (one
  package per recipe), `integration_release.py:125-134` (64 MiB cap) and `:251` (Pages limit),
  `install.py:413-604`, `desktop/etc/mkinitcpio.conf.d/arch-niri-dux.conf`, mkinitcpio 1126-1140 (drop-in
  order), limine's `90-mkinitcpio-install.hook` targets.
- Omarchy (`omacom/omarchy@quattro`): `install/hardware/nvidia.sh`, `bin/omarchy-hw-nvidia{,-gsp,
  -without-gsp,-display}`, `bin/omarchy-hw-hybrid-gpu`, `etc/mkinitcpio.conf.d/omarchy_hooks.conf`,
  `install/hardware/vulkan.sh`, `default/hypr/nvidia.lua`, `bin/omarchy-apply-hardware`.
  `omarchy-pkgs@master`: `pkgbuilds/nvidia-580xx-utils` (+ `.omarchy/package.json`),
  `pkgbuilds/lib32-nvidia-580xx-utils`. `omarchy-iso@quattro`: `builder/archinstall.packages`,
  `builder/build-iso.sh`. Their NVIDIA migrations were not read individually; the report covers them.
- `pkgs.omarchy.org/stable/x86_64/omarchy.db` and detached signatures (key `F0134EE680CAC571`).
- AUR RPC and PKGBUILD for `nvidia-580xx-utils` 580.196.07-1.
- Trial resolve against `archive.archlinux.org/repos/2026/11/10` with multilib: base 701 packages, both
  lists add 60 packages / 632 MB from Arch.
