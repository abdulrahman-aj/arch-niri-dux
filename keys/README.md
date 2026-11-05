# Public build signing keys

`trusted-packagers.gpg` contains only the public signing keys needed by the
pinned bootstrap ISO and package closure. `fingerprints.txt` records the issuer
fingerprints extracted from their signatures. These were exported from the
Arch/Omarchy package keyring after the initial signature-verified build.

The build uses this repository keyring, so it does not require Omarchy or a
previously initialized host pacman keyring. No private key material is included.
The build checks distribution packages against both hashes and signatures. The four
Dux AUR packages use reviewed recipes and pinned upstream source hashes instead.
Their build provenance is recorded separately in the output manifest.

When intentionally updating the package lock, check new signing fingerprints
against the source distribution's public keyring before adding their public keys.

`integration-signing.gpg` is our public release key, and
`integration-signing.fingerprint` pins its primary identity. CI receives only
the signing subkey. The [release-key guide](RECOVERY.md) covers what to back up,
how to inspect and access both private keys, CI secret restoration, and the
remaining rotation and revocation limitations. See
[release maintenance](../packages/RELEASING.md) for ordinary publication.

The added signing subkey `4C13F5553C2D87AD23220A073EC2E354F61AC9F6`
belongs to Hyacinthe Cartiaux's primary key
`DB310957BB30360A67311D87886D7ACFED689539`, published in Arch's
[maintainer directory](https://archlinux.org/people/package-maintainers/#hyacinthe).
Its binding and Arch master-key certifications were checked in the distribution
keyring before verifying the pinned strace and nss-mdns payloads.
