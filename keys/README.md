# Public build signing keys

`trusted-packagers.gpg` contains only the public signing keys needed by the
pinned bootstrap ISO and package closure. `fingerprints.txt` records the issuer
fingerprints extracted from their signatures. These were exported from the
Arch/Omarchy package keyring after the initial signature-verified build.

The build uses this repository keyring, so it does not require Omarchy or a
previously initialized host pacman keyring. No private key material is included.
Distribution packages are checked against both hashes and signatures. The four
Dux AUR packages use reviewed recipes and pinned upstream source hashes instead;
their build provenance is recorded separately in the output manifest.

When intentionally updating the package lock, check new signing fingerprints
against the source distribution's public keyring before adding their public keys.
