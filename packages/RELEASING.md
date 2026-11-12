# Integration releases

[Package inputs](README.md) describes what our signed repository supplies and
how the baseline metapackage controls app additions. `/etc/skel` seeds
missing personal defaults for new accounts; package upgrades never touch home. Pacman preserves
edits to files listed in the integration recipe's `backup` array through its
`.pacnew` behavior.

## Publish

1. Change `pkgver`/`pkgrel` in `integration/PKGBUILD` and submit a reviewed PR.
   Dux app updates arrive as PRs from the `dux-apps.yml` workflow. GitHub does
   not run CI on PRs created by Actions; close and reopen one to run it. That
   workflow needs "Allow GitHub Actions to create and approve pull requests".
   A merged app update ships with the next integration version.
2. Run `make lock` when refreshing inputs, then `make test`, `make iso-development`, and `make verify`
   on the candidate. `make lock` moves the Arch archive date and the vendored Dux files forward together.
   Update recorded evidence for changes affecting installation or the desktop.
3. Merge, then tag that main commit as `v<version>-<pkgrel>` and push
   the tag. This step publishes. Main merges alone do not
   deliver updates to devices.
4. The tag workflow builds as an unprivileged user, signs in the protected
   `integration-release` environment, publishes GitHub Release assets, and deploys
   the signed repository to GitHub Pages. The build job has no signing secret. The separate signing job uses the pinned
   Nix toolchain and performs no live `pacman -Syu`. It checks manifest hashes
   and source inputs before signing with the pinned subkey.
5. Run `make iso` to build with those exact authenticated packages, rather than
   independently rebuilding packages under the same version.
   Then `make verify-release` runs offline acceptance followed by a networked VM
   boot. Real HTTPS repository/package verification uses the installed offline
   trust, checks exact release hashes, and preserves personal config.
   Select the artifact when retaining several ISOs:
   `make verify-release ARGS='--iso out/NAME.iso --directory out/verify-release-VERSION'`.

Package publication and Pages deployment use `v*` tags. ISO authorization also
requires the `main` branch in the protected release environment, as described below.
The publisher also requires the tagged commit to belong to main and refuses a
version older than an already published release. Each release contains every
Dux app. An app version already published keeps its signed bytes. The publisher
refuses an app downgrade, or changed package contents without a new `pkgrel`. A failed Pages deployment can
be retried. The publisher reuses the published release's package bytes. Published
integration packages within the checked-in history boundary remain available for clients with older cached databases.
Pages serves only the current Dux app versions, keeping it under its 1 GB limit;
GitHub Releases keep every version.
Do not replace release assets or force-move published tags. Make a new version.

Build ISOs from a trusted checkout. Its public key provides the initial release
verification. Installed systems trust the keyring provisioned offline
by their ISO. Retrying publication preserves package bytes. Signatures and
repository metadata are regenerated. Signatures do not prevent a server replaying an
older signed repository. Release CI runs unit tests and makepkg, not graphical
VM acceptance. Keep the candidate's local VM evidence with the PR.

## Authorize and distribute an ISO

Before each release, update the README verification section to name its intended
ISO version and check its public certificate link and digest. Package releases
are immutable: nothing can be added to a release after publication, including
ISO checksums. The signature travels with the ISO instead.

Dispatch the ISO workflow at **main**, with the package release tag as an input.
GitHub runs the workflow version at the dispatch ref. Dispatching at an old tag
such as `v0.3.0-2` would still run the old release-upload code from that tag.
Merge workflow fixes to main before dispatch; never move an existing release tag.

One-time environment setup after merging this workflow: in Settings → Environments
→ `integration-release` → Deployment branches and tags, add an exact **branch**
rule for `main` alongside the existing `v*` tag rule. Keep the required maintainer
reviewer and signing secret unchanged. This permits the updated main workflow to
authorize an existing release such as `v0.3.0-2`; the tag alone no longer selects
signing code. The workflow rejects dispatch from any other ref.

Build from a clean trusted checkout of the package tag and retain the local
acceptance evidence from the publish steps above. Choose exactly one release ISO.
Copy it to the canonical filename derived from the tag's version, then prepare
its checksum:

```sh
cp out/CHOSEN.iso out/arch-niri-dux-0.3.0-x86_64.iso
make iso-checksums ARGS='--tag v0.3.0-2 --iso out/arch-niri-dux-0.3.0-x86_64.iso'
```

This light target hashes only the explicit file and writes
`out/iso-checksums/SHA256SUMS`. It refuses a noncanonical filename or an existing
checksum file. Retain or explicitly remove an earlier candidate's checksum file
before preparing another. Do not sign the build's `out/SHA256SUMS`, which may
include other ISOs retained in `out/`.

Dispatch **ISO checksum authorization**, replacing `DIGEST` with the printed
64-character digest:

```sh
gh workflow run iso-checksums.yml --ref main -f tag=v0.3.0-2 -f sha256=DIGEST
```

The secret-free validation job checks the selected tag is a published package
release and its commit belongs to the dispatched main history. Its summary shows
the canonical filename, digest, ISO source commit and workflow commit. Compare
them against your trusted checkout and qualified artifact **before approving**
the `integration-release` job. Approval means "I authorize these exact ISO bytes."
ISOs are not byte-reproducible. CI does not receive, build, inspect or reproduce
the ISO. A compromised local builder or mistaken approval can therefore authorize
a malicious ISO. The shared signing subkey shares the package signer's compromise
consequences.

After approval, the signing job repeats validation and requires the same source
commit as before approval. It uses the main workflow's pinned Nix tools and key
pins, imports the existing CI secret into a disposable GnuPG home, and signs
canonical `SHA256SUMS` with the exact pinned subkey. It verifies the result and
removes the private material and agent. Both checksum files appear in the job
summary and in the `iso-checksums-TAG-ATTEMPT` run artifact, retained for 30 days.
No release assets or tags are changed, and the workflow needs only read access
to repository contents. A rerun creates a fresh signature and a separate artifact.

Download and extract the run artifact promptly from the Actions run page, or use:

```sh
gh run download RUN_ID --name iso-checksums-v0.3.0-2-1 --dir out/iso-distribution
```

Keep `SHA256SUMS` and `SHA256SUMS.sig` beside the canonical ISO and share all three
together through Google Drive, USB or another chosen host. Archive them yourself;
the Actions artifact is temporary. Friends obtain the public key from `keys/`
in this repository and use the trusted README verification instructions.
Canonical ISO filenames omit `pkgrel`, so keep each tag's metadata separate and
tell friends which release to use. Do not silently substitute a different ISO
for an already distributed authorization.

## Signing and recovery

The [release-key guide](../keys/RECOVERY.md) explains the master and signing keys,
the encrypted backup, inspection, access to each key, and restoring the GitHub
signing secret. **Keep the full encrypted backup off-machine and its password
separately.** GitHub stores only the working signing subkey.

Initial provisioning uses `scripts/release_key.py --passphrase-file PATH`.
Ordinary releases use the existing CI secret. Preparation and `--restore` are offline by default. Only explicit `--upload`
replaces the live secret; restoration does not rotate or renew keys.

See [rotation and compromise
recovery](../keys/RECOVERY.md#rotation-and-compromise-recovery) for
trust-transition limits and required qualification.
