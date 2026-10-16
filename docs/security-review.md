# Security review — 16 October 2026

Three independent reviews covered access controls, report isolation, and storage/watchers. The review used synthetic files and temporary databases. The fixes have regression tests rather than relying on configuration alone.

## Findings fixed

- **Password rotation race:** an unlock already checking the old password could issue a session after rotation. Verification and session insertion now share the rotation/revocation lock.
- **Snapshot and file containment:** unexpected filesystem symlinks could be copied into snapshots or followed by a directory-index request. Descriptor-relative, no-follow opens now protect package copying, validation and serving, including the index file.
- **Watched-folder races:** files or directories swapped after inspection could redirect reads or consumption. The watcher pins directories, checks file identity and reads/removes entries relative to those descriptors. Symlinks, special files and oversized/growing submissions are rejected.
- **Resource bounds:** request worker count, body duration, package size, file count, depth and response bytes are bounded. Ambiguous Content-Length headers are rejected; non-ASCII invalid API keys receive an authentication error.
- **Local Host validation:** requests must address the configured Portal host or its local listening address, reducing DNS-rebinding exposure for a local app without its own login.

Package copies sync files and supported directory metadata before successful imports can consume watched inputs. Network filesystems still depend on their own durability guarantees.

## Validation

The unit/HTTP suite includes deterministic password-rotation/revocation races, symlink swaps, directory-index escapes, special files, growth and resource bounds. Browser suites cover sandbox isolation, packaged assets, password-protected sharing, API access, key rotation and ordinary workflows. See [validation](validation.md).

Report scripts and external resources remain supported inside an opaque-origin sandbox. The app is intended to run as a single instance per database/storage pair. Remote private routes require an authentication proxy; the API key and share access checks are separate. This review does not establish that arbitrary third-party reports or the surrounding host infrastructure are free of vulnerabilities.

## Storage mount aliases

Watcher overlap checks also recognise Docker bind aliases and nested filesystem mounts. A report folder exposed at both `/storage/reports` and another mounted path cannot be added as an inbox through the alternate path.
