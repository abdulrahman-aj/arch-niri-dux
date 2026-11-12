# Building and testing

Building requires Linux, Nix and Make. Allow at least 30 GiB of free disk space for the build and about
2.5 GiB for the ISO. VM verification also needs KVM.

```sh
make iso
make verify
```

`make iso` builds the ISO from the signed packages of the release in the recipe. To build unreleased
integration changes, run `make iso-development` instead. To test-drive the installer by hand in a window, run `make try` (humans only; agents use `make verify-fast ARGS=--keep`). It asks whether to open an existing VM, create a new one, or delete one. Each VM keeps its disk under `~/.local/share/arch-niri-dux/try/<name>/`, boots the newest `out/*.iso` with the disk first in boot order (so an installed system boots when present, otherwise the installer), and gets 8 GiB of RAM. Build an ISO first with `make iso-development` when `out/` holds none. Arch packages come from the date pinned in
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
