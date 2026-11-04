# Package inputs

`integration/PKGBUILD` builds our owning `arch-niri-dux` package and the separate
`arch-niri-dux-baseline` metapackage. The baseline depends on `packages.target`;
dropping it allows removing optional baseline apps while retaining integration
updates. Existing prototype ISOs are not automatically migrated. App retirement
will use targeted reviewed transitions, without general orphan cleanup.

`scripts/build-integration.py` prepares a deterministic local source archive and
records its checksum for makepkg. Release ISOs fetch the exact signed release
artifacts; development ISOs build the local recipe. The ISO installs these artifacts
instead of copying an unowned overlay. The output manifest records their hashes
and source provenance separately from signed upstream and reviewed AUR inputs.

`closure.lock.json` pins the signed Arch/Omarchy binary packages. Arch's official
repositories take precedence over Omarchy on the installed system. Papers and
Snapshot also come from Arch's signed packages. New entries extend the closure
without changing existing pins unless explicitly refreshed.

`aur/` contains unmodified, reviewed recipes for Dux Greeter, Search, Calendar
and Files 2.0.0. `aur-recipes.json` records their AUR commits and file hashes.
Builds enforce the vendored file hashes; the commits record their provenance.
Their maintainer, `bbedward`, is a Dux Labs member and upstream contributor.
Release-asset hashes were checked against Dux's GitHub release metadata; the
recipes also verify every source through `makepkg`. These packages are built as
an unprivileged user inside the disposable builder, not downloaded or built
during installation. They do not carry Arch package signatures.

`out/packages.manifest.json` records the signed closure and the locally built
packages with their hashes and recipe provenance. Installed AUR packages receive
updates through Yay or Dux's system updater; future recipe changes still require
review. Replacing these AUR sources is tracked in TODO.md.
