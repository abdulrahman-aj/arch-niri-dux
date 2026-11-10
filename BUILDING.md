# Building and testing

Building requires Linux, Nix and Make. Allow free disk space for the build and
about 2.5 GiB for the ISO; the free-space guard is documented under
[Verification](VERIFICATION.md). VM verification also needs KVM.

```sh
make iso
make verify
```

`make iso` builds the ISO from the signed packages of the release in the recipe. To build unreleased
integration changes, run `make iso-development` instead. The build writes the ISO and its checksums to
`out/`. [Verification](VERIFICATION.md) lists the test targets, VM requirements and current limits, and
[release maintenance](packages/RELEASING.md) covers publishing.

Each checkout gets its own build state in `~/.cache/arch-niri-dux-build-<checkout-path-hash>` (under
`$XDG_CACHE_HOME` when set). Set `BUILD_STATE=/path/with/space` to use another directory. You manage an
explicit `BUILD_STATE` yourself, and `make gc` leaves it alone. All build states share one cache of verified
downloads in `$XDG_CACHE_HOME/arch-niri-dux-downloads` (default `~/.cache`). Builds recheck each file's hash
or signature before using it. The cache never deletes old versions, and deleting the whole cache is safe.

Signed binary packages and reviewed Dux app recipes have separate pins. See [package
inputs](packages/README.md) and [desktop configuration](desktop/README.md#niri-configuration). Rebuilding
our packages from the same pinned inputs does not produce byte-identical packages.
