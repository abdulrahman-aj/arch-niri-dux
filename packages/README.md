# Package inputs

[`integration/PKGBUILD`](integration/PKGBUILD) builds two packages.
`arch-niri-dux` installs the desktop configuration and scripts. The
`arch-niri-dux-baseline` metapackage requires the packages listed in
`packages.target`. Removing it lets you remove optional apps while keeping
integration updates.

`scripts/integration_build.py` prepares a deterministic local source archive and
records its checksum for makepkg. Release ISOs fetch the exact signed release
artifacts. Development ISOs build the local recipe. The installer installs the
packages, and pacman tracks their files.

## Repositories

`packages/archive-date` pins every Arch binary package in the offline ISO
payload to one Arch Linux Archive snapshot
(`https://archive.archlinux.org/repos/YYYY/MM/DD`), and
`packages/omarchy-pin.json` pins the two Omarchy packages
(`limine-mkinitcpio-hook`, `limine-snapper-sync`) by hash and signature. The
installed repository order is arch-niri-dux at
`https://abdulrahman-aj.github.io/arch-niri-dux/x86_64`, then core and extra,
then omarchy.
Our repository comes first so another repository cannot shadow our packages.
It also carries the exact pinned Omarchy boot packages, signed with our
release key, so installed machines take only the versions a release verified.
The installed `[omarchy]` section sets `Usage = Install Sync`, so `pacman
-Syu` never upgrades from Omarchy. Our repository is listed first and wins by
name regardless of version, so explicit installs of the two boot packages
take our pinned copies too; Omarchy's repo stays usable for explicit installs
of anything else.
The date freezes the build payload only: installed systems track the rolling
mirrors in `/etc/pacman.d/arch-niri-dux.conf`, so `pacman -Syu` keeps updating
Arch packages after installation. The integration repository
retains `Required TrustedOnly`; other repositories inherit `Required
DatabaseOptional`.

Run `make lock` to refresh the archive date, the Omarchy pin, and the vendored
Dux files. It picks the latest available archive snapshot, vendors the Niri
configuration from the dux-shell release matching that snapshot's dux-shell,
and rewrites the pins; the Omarchy pin records each package's dependencies, so
builds never read Omarchy's database. The build guest resolves the requested
package lists against the pinned date, downloads them with pacman, and checks
every signature against `keys/trusted-packagers.gpg`; the manifest records what
was downloaded. Builds reuse the shared verified download cache.

## Dux app builds

`aur/` contains reviewed recipes for Dux Greeter, Search, Calendar and Files.
`aur-recipes.json` pins every recipe file's hash, and builds refuse changed
files. `scripts/dux_apps.py` builds them as an unprivileged user, and
`makepkg` verifies every upstream source checksum. Integration releases sign and
publish them in our repository, like our own packages. Installed systems never
use the AUR.

The scheduled `dux-apps.yml` workflow tracks upstream GitHub releases rather
than the AUR, so updates do not wait for or trust it. When Dux tags a
release, `scripts/bump_dux_apps.py` updates `pkgver`, `pkgrel`, checksums,
recipe hashes, `source_date_epoch` and the vendored upstream trust files.
Policy, service, desktop, portal and greeter configuration files are local
sources next to their recipes, so upstream changes appear as readable PR diffs. Release-asset checksums must equal
GitHub's published digests. Repository files at the release tag have no
published digest. The workflow opens a PR and never merges or publishes.

## ISO inventory

`scripts/iso_payload.py` finalizes the ISO repository, checksums and combined
`out/packages.manifest.json` after the package producers finish. It rejects
missing, unexpected or mismatched packages and checks the generated database
against their declared inventory.

The manifest records the pinned archive date, the downloaded Arch packages, and
our packages with their hashes and recipe provenance. Dux's system updater or
`pacman -Syu` updates everything from signed repositories. `base-devel` provides `sudo`, which the installer
configures for `wheel`, and `fakeroot`, which Dux's updater uses to check for
updates without root.

See [boot-policy upgrades](../desktop/README.md#boot-policy-upgrades) for
package transaction hooks and first-install ordering.
