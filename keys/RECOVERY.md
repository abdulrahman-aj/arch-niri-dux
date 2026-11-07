# Release signing and recovery

The non-expiring certification primary is the trust anchor. Keep its full encrypted
backup off-machine and its password separately. `integration-signing.fingerprint`
pins that primary; `integration-signing.subkey` selects one release signer.
`integration-signing.gpg` retains all public bindings and revocations. CI receives
only the selected subkey secret and a primary stub.

## Prepare and restore

Use a trusted offline machine and a disposable GnuPG home. Never import backups
into your personal keyring. Initial preparation is:

```sh
python scripts/release_key.py --passphrase-file /secure/password \
  --backup /secure/release-backup.gpg
```

Preparation writes a new encrypted full backup, public certificate and signer pin.
It refuses to overwrite an existing backup. Review these public changes through a
PR. Upload is a separate deliberate operation using `--upload`, after testing
restoration and checking the selected fingerprint:

```sh
python scripts/release_key.py --passphrase-file /secure/password \
  --restore /secure/release-backup.gpg --backup /secure/release-backup.gpg --upload
```

`--restore` restores existing material; it does not create or revoke subkeys.
It merges the checked-in public certificate before selecting a signer, preserving
later revocations. A stale backup without the selected private subkey fails.
Use `--signer FULL_SUBKEY_FINGERPRINT` to select a different available signer.
Without `--upload`, no GitHub secret changes.

## Routine rotation

Rotate before expiry. The original production subkey expires 2028-11-03.

1. Decrypt the full backup into a disposable offline GnuPG home, then import the
   current checked-in public certificate. Inspect `gpg --with-subkey-fingerprint
   --list-secret-keys`. Retain every public subkey and revocation.
2. Use `gpg --quick-add-key PRIMARY rsa3072 sign 2y` to certify replacement B.
   Export the full private identity, encrypt it with the recovery password, and
   retain it as a new backup with mode 0600. Never overwrite the previous copy.
3. Prepare with `--restore NEW_BACKUP --backup NEW_BACKUP --signer A`.
   Publish an A-signed transition release containing B's public binding, while A
   is still safe and valid. Review the certificate through a PR first.
4. Test restoration, then prepare with `--signer B`, review the pin change, and
   deliberately upload B with `--upload`. The next fresh release uses B.
5. Destroy the disposable home and plaintext exports after checking backups.
   Keep the primary offline during ordinary publication.

A client that received the transition can upgrade under B. No overlap duration
guarantees that every client receives it. Clients that missed transitions fail
signature verification and use the authenticated recovery procedure below.
Do not weaken `SigLevel` or fetch unauthenticated keys.

## Rotation and compromise recovery

Stop publication and contain compromised CI, credentials, GitHub and Pages access
first. On the offline maintenance machine, merge the latest certificate, then use
`gpg --edit-key PRIMARY`, select the compromised subkey with `key N`, and use
`revkey` with the compromise reason. Certify a replacement and make a fresh full
encrypted backup. Never revoke the safe primary. Retain the revoked public subkey
and its revocation in the exported certificate. Do not add the primary to a pacman
revoked list; that would disable its replacements too.

Review `keys/integration-history-start.json` in the same recovery PR. Move its tag
to the first replacement release and raise every package's minimum version,
including each Dux app, above its last published version. Rebuild reviewed sources
with fresh versions. Neither the shell downloader nor the Python publisher accepts
history before those boundaries. Review version increases against the last trusted
release: a boundary reset deliberately removes the older comparison history.
Do not re-sign old bytes authenticated only by the compromised key. Immutable
GitHub archives remain historical records; issue an advisory identifying them.
Old cached transactions cease to be supported across the boundary. Tell users
in the advisory not to restore snapshots taken before the revocation, and ship
the revocation release with a system migration that deletes root snapshots
older than the revocation.

An emergency transition signed by the compromised subkey cannot establish trust.
Affected clients must recover their certificate before upgrading. Offline clients
continue trusting that subkey until they learn its revocation. Recovery restores
update trust; a device on which an attacker already executed code may need reinstalling.
Primary loss or compromise requires separate trust replacement and is outside this
subkey procedure.

## Recover a client that missed transitions

Obtain the complete current public certificate and its SHA-256 digest from the
trusted offline maintainer after containment. A digest next to an untrusted download
does not authenticate freshness. Transfer both through the authenticated maintenance
channel, then run:

```sh
sudo arch-niri-dux-keyring-recover /path/current-certificate.gpg --sha256 DIGEST
sudo pacman -Syu
```

The installed helper validates the digest before touching pacman trust. In a
private temporary home it requires the single installed primary and a usable
master-authorized signing subkey. It rejects private material and unrelated
identities. The primary must already be trusted in pacman's keyring. The helper
merges public updates using `pacman-key --add` and checks the trust database; it
never downloads keys, imports ownertrust or signs a new primary. Repeating it is
safe. OpenPGP merging preserves learned revocations even if an old certificate
is later replayed. The next signed package upgrade updates package-owned keyring
files and runs the normal keyring hook.

Expired old signing subkeys use the same procedure. The primary must remain
valid. Raw pacman reports its own signature/key errors; these errors can indicate
a missed transition, but the maintainer must establish the cause and supply the
current authenticated certificate.

## Qualification

`make test` uses disposable GnuPG homes and real pacman roots. `make verify`
runs the shared lifecycle probe using the installed helper in a disposable VM.
VM work runs only on msi-dux. Production keys and the real GitHub secret are never
used in qualification. Tag-time release upload, approval, artifact transfer and
Pages deployment still require the first deliberate real release.
