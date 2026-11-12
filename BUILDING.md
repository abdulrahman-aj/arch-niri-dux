# Building and testing

Building requires Linux, Nix and Make. Allow at least 30 GiB of free disk space for the build and about
2.5 GiB for the ISO. VM verification also needs KVM.

```sh
make iso
make verify
```

`make iso` builds the ISO from the signed packages of the release in the recipe. To build unreleased
integration changes, run `make iso-development` instead. To test-drive the installer by hand in a window, run `make try` (humans only; agents use `make verify-fast ARGS=--keep`). It lists your VMs and prompts for a number or name to open, `new` to create one, or `delete` to remove one (asking which, then confirming). Creating a VM asks for a name, what to install (the latest release or this checkout), RAM in GiB, and disk in GiB; the answers are stored with the VM and reused on every open, so nothing is asked again. Each VM keeps its disk under `~/.local/share/arch-niri-dux/try/<name>/` and boots the disk first, with its stored install source attached second (so an installed system boots when present, otherwise that source's installer). Release installers are plain `out/*.iso` files built with `make iso`; checkout installers end in `-development.iso` and are built with `make iso-development`, so both kinds coexist in `out/`. When the VM's ISO is missing, `make try` offers to build it now with the matching make target (default yes); when the checkout ISO predates the checkout's HEAD commit, it offers a rebuild (default no). Release ISOs are never treated as stale. Arch packages come from the date pinned in
`packages/archive-date`; run `make lock` to move it forward. The build writes the ISO and its checksums to
`out/`. An ISO stays installable only while its install keyring's earliest signing key is still valid;
the installer refuses before touching the disk once a key has expired, so replace distributed ISOs before then. [Verification](VERIFICATION.md) lists the test targets, VM requirements and current limits, and
[release maintenance](packages/RELEASING.md) covers publishing.

Each checkout gets its own build state in `~/.cache/arch-niri-dux-build-<checkout-path-hash>` (under
`$XDG_CACHE_HOME` when set). Set `BUILD_STATE=/path/with/space` to use another directory. You manage an
explicit `BUILD_STATE` yourself, and `make gc` leaves it alone. All build states share one cache of verified
downloads in `$XDG_CACHE_HOME/arch-niri-dux-downloads` (default `~/.cache`). Builds recheck each file's hash
or signature before using it. The cache never deletes old versions, and deleting the whole cache is safe.

Signed binary packages and reviewed Dux app recipes have separate pins. See [package
inputs](packages/README.md) and [desktop configuration](desktop/README.md#niri-configuration). Rebuilding
our packages from the same pinned inputs does not produce byte-identical packages.
