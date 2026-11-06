# Package inputs

`integration/PKGBUILD` builds two packages. `arch-niri-dux` installs the desktop
configuration and scripts. The `arch-niri-dux-baseline` metapackage requires the
packages listed in `packages.target`. Removing it lets you remove optional apps
while keeping integration updates. App retirement will use targeted reviewed
transitions without general orphan cleanup.

`scripts/build-integration.py` prepares a deterministic local source archive and
records its checksum for makepkg. Release ISOs fetch the exact signed release
artifacts. Development ISOs build the local recipe. The installer installs the
packages, and pacman tracks their files.

`closure.lock.json` pins the signed Arch/Omarchy binary packages. The installed repository order is core and extra through
`https://geo.mirror.pkgbuild.com/$repo/os/$arch`, then omarchy, then our signed
integration repository. The integration repository retains `Required TrustedOnly`; other
repositories inherit `Required DatabaseOptional`. Papers and
Snapshot also come from Arch's signed packages. New entries extend the closure.
A lock refresh can change existing pins.

`aur/` contains reviewed recipes for Dux Greeter, Search, Calendar and Files.
Their maintainer, `bbedward`, is a Dux Labs member. `aur-recipes.json` pins
every recipe file's hash, and builds refuse changed files. `scripts/build-dux-apps.py` builds them as an
unprivileged user, and `makepkg` verifies every upstream source checksum.
Integration releases sign and publish them in our repository, like our own
packages. Installed systems never use the AUR.

The scheduled `dux-apps.yml` workflow tracks upstream GitHub releases rather
than the AUR, so updates do not wait for or trust it. When Dux tags a
release, `scripts/bump-dux-apps.py` changes only `pkgver`, `pkgrel`, checksums,
recipe hashes and `source_date_epoch`. Release-asset checksums must equal
GitHub's published digests. Repository files at the release tag have no
published digest. The workflow opens a PR and never merges or publishes.

`scripts/iso_payload.py` finalizes the ISO repository, checksums and combined
`out/packages.manifest.json` after the package producers finish. It rejects
missing, unexpected or mismatched packages and checks the generated database
against their declared inventory.

The manifest records the signed closure and our packages with their hashes and
recipe provenance. Dux's system updater or `pacman -Syu` updates everything from
signed repositories. `base-devel` stays: it provides `sudo`, which the installer
configures for `wheel`, and `fakeroot`, which Dux's updater uses to check for
updates without root.

Every integration package upgrade rebuilds boot artifacts through its ALPM hook.
See [boot policy and first-install ordering](../desktop/README.md#packaged-desktop-integration)
for the upgrade-only trigger and explicit installer rebuild.
