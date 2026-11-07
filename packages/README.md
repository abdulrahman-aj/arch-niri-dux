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

`closure.lock.json` pins the signed Arch/Omarchy binary packages. The installed
repository order is core and extra through
`https://geo.mirror.pkgbuild.com/$repo/os/$arch`, then omarchy, then
arch-niri-dux at `https://abdulrahman-aj.github.io/arch-niri-dux/x86_64`.
Omarchy uses `https://pkgs.omarchy.org/stable/$arch`. The package ships this
configuration in `/etc/pacman.d/arch-niri-dux.conf`. The integration repository
retains `Required TrustedOnly`; other repositories inherit `Required
DatabaseOptional`. New entries extend the closure. A lock refresh can change
existing pins.

## Dux app builds

`aur/` contains reviewed recipes for Dux Greeter, Search, Calendar and Files.
`aur-recipes.json` pins every recipe file's hash, and builds refuse changed
files. `scripts/dux_apps.py` builds them as an unprivileged user, and
`makepkg` verifies every upstream source checksum. Integration releases sign and
publish them in our repository, like our own packages. Installed systems never
use the AUR.

The scheduled `dux-apps.yml` workflow tracks upstream GitHub releases rather
than the AUR, so updates do not wait for or trust it. When Dux tags a
release, `scripts/bump_dux_apps.py` changes only `pkgver`, `pkgrel`, checksums,
recipe hashes and `source_date_epoch`. Release-asset checksums must equal
GitHub's published digests. Repository files at the release tag have no
published digest. The workflow opens a PR and never merges or publishes.

## ISO inventory

`scripts/iso_payload.py` finalizes the ISO repository, checksums and combined
`out/packages.manifest.json` after the package producers finish. It rejects
missing, unexpected or mismatched packages and checks the generated database
against their declared inventory.

The manifest records the signed closure and our packages with their hashes and
recipe provenance. Dux's system updater or `pacman -Syu` updates everything from
signed repositories. `base-devel` provides `sudo`, which the installer
configures for `wheel`, and `fakeroot`, which Dux's updater uses to check for
updates without root.

See [boot-policy upgrades](../desktop/README.md#boot-policy-upgrades) for
package transaction hooks and first-install ordering.
