# Public build signing keys

`trusted-packagers.gpg` contains only the public signing keys needed by the
pinned bootstrap ISO and package closure. `fingerprints.txt` records the issuer
fingerprints extracted from their signatures. These were exported from the
Arch/Omarchy package keyring after the initial signature-verified build.

The build uses this repository keyring, so it does not require Omarchy or a
previously initialized host pacman keyring. No private key material is included.
See [package inputs](../packages/README.md) for binary-package and Dux recipe
verification.

When intentionally updating the package lock, check new signing fingerprints
against the source distribution's public keyring before adding their public keys.

`integration-signing.gpg` is our public release key, and
`integration-signing.fingerprint` pins its primary identity. CI receives only
the signing subkey. The [release-key guide](RECOVERY.md) covers what to back up,
how to inspect and access both private keys, CI secret restoration, and the
remaining rotation and revocation limitations. See
[release maintenance](../packages/RELEASING.md) for ordinary publication.
