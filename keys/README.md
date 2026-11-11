# Public build signing keys

`trusted-packagers.gpg` contains only the public signing keys needed by the
pinned bootstrap ISO and the two pinned Omarchy packages. `fingerprints.txt` records the issuer
fingerprints extracted from their signatures. These were exported from the
Arch/Omarchy package keyring after the initial signature-verified build.

The build uses this repository keyring to bootstrap trust, so it does not require Omarchy or a
previously initialized host pacman keyring. Arch packages are verified by pacman against the
`archlinux-keyring` refreshed from the pinned archive date inside the build guest; the two
Omarchy packages are verified against this keyring. No private key material is included.
See [package inputs](../packages/README.md) for binary-package and Dux recipe
verification.

When intentionally updating the Omarchy pin, check new signing fingerprints
against the source distribution's public keyring before adding their public keys. Arch keys need
no manual check: they arrive through the pinned date's own keyring package.

`integration-signing.gpg` is our public release key, and
`integration-signing.fingerprint` pins its primary identity. CI receives only
the signing subkey. The [release-key guide](RECOVERY.md) covers what to back up,
how to inspect and access both private keys, CI secret restoration, and the
rotation, revocation and authenticated client recovery. See
[release maintenance](../packages/RELEASING.md) for ordinary publication.

The offline installer requires trusted signatures on packages. Its ISO keyring
contains the pinned Arch/Omarchy package signers and our release public key.
The offline database is generated during ISO assembly and is unsigned; its
metadata cannot authorize unsigned packages. Checksums on the same medium detect
corruption, but do not authenticate it.

Development ISOs sign locally built packages with a fresh, disposable build key.
Only its public key enters that ISO, and the builder deletes the private key.
Development keys do not authorize updates on installed systems.

These checks assume the installer and its embedded public keys are intact. A
whole-medium rewrite can replace both. The
[ISO checksum workflow](../packages/RELEASING.md#authorize-and-distribute-an-iso)
uses our existing CI-only release subkey to sign the maintainer-approved hash of
one locally built release ISO. Its checksum and signature travel with the ISO;
the public key remains in this directory. Friends must follow the [README
verification steps](../README.md#verify-a-prebuilt-iso) on a trusted machine before flashing.
The certificate digest in those trusted instructions authenticates the public key.
CI approval authorizes the chosen bytes; it does not prove their build provenance.
Package signatures
also do not prevent replay of an older signed package through a rewritten database.
