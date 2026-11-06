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

`aur/` contains unmodified, reviewed recipes for Dux Greeter, Search, Calendar
and Files 2.1.0. `aur-recipes.json` records their AUR commits and file hashes.
Builds enforce the vendored file hashes. The commits record their provenance.
Their maintainer, `bbedward`, is a Dux Labs member and upstream contributor.
Release-asset hashes were checked against Dux's GitHub release metadata. The
recipes also verify every source through `makepkg`. The disposable builder builds
these packages as an unprivileged user. The offline installer uses the resulting
packages. They do not carry Arch package signatures.

`scripts/iso_payload.py` finalizes the ISO repository, checksums and combined
`out/packages.manifest.json` after the package producers finish. It rejects
missing, unexpected or mismatched packages and checks the generated database
against their declared inventory. The AUR builder reports only its own results.
With no AUR recipes the finalizer needs no AUR result file.

The manifest records the signed closure and locally built packages with their
hashes and recipe provenance. Yay or Dux's system updater updates installed AUR
packages. Recipe changes require review. Replacing these AUR sources is tracked in
[issue #47](https://github.com/abdulrahman-aj/arch-niri-dux/issues/47).

Every integration package upgrade rebuilds boot artifacts through its ALPM hook.
See [boot policy and first-install ordering](../desktop/README.md#packaged-desktop-integration)
for the upgrade-only trigger and explicit installer rebuild.
