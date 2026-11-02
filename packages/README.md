# Package inputs

`closure.lock.json` pins the signed Arch/Omarchy binary packages. Arch's official
repositories take precedence over Omarchy on the installed system. The lock
currently holds 625 signed distribution packages (604 before Papers/Snapshot)
plus four reviewed Dux AUR packages; new entries only extend the closure and
never change existing pins without an explicit refresh.

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
