# minio — gotchas

### MinIO is archived; object storage runs Silo, a maintained fork

- **Symptom:** The newest pullable `minio/minio` image is permanently unpatched — the final
  MinIO release, which fixed a privilege-escalation CVE, was never published to any
  registry.
- **Cause:** Both `minio/minio` and `minio/mc` were archived upstream in 2026.
- **Fix:** The image is Silo. It preserves the `MINIO_*` environment variables and the
  on-disk format, so this was an image swap with no data migration, and it is reversible:
  MinIO reads Silo-written data and vice versa, both directions verified. See
  [ADR 0008](../../docs/adr/0008-object-storage-replacement.md).
- **Affected versions:** MinIO archived in 2026; the pin is
  `SILO_VERSION=RELEASE.2026-09-03T13-18-01Z`.

### The volume is still named `minio-data`, and stays that way

- **Symptom:** Renaming the volume orphans its data with no error anywhere.
- **Cause:** A renamed volume is a new, empty one, and nothing reports the old one being
  left behind (ADR 0004).
- **Fix:** Leave the identifier `minio-data` frozen. The module directory is
  `services/minio/` for the same reason of least surprise; ADR 0004 freezes volume
  identifiers, not paths.
- **Affected versions:** Not version-specific

### The same image supplies `mc`

- **Symptom:** There is no separate client image anywhere in the stack, which looks like an
  omission.
- **Cause:** `minio-init` overrides the entrypoint to run the client out of the server
  image.
- **Fix:** Leave it. Adding a second image would add a second pin to keep in step.
- **Affected versions:** Not version-specific

### Volume size is not a data-integrity signal

- **Symptom:** The volume grows and shrinks on its own, and neither movement corresponds to
  an object you wrote.
- **Cause:** `.minio.sys` churns constantly through background healing.
- **Fix:** Check bucket and object listings instead — `pixi run mc` opens a shell with the
  client configured.
- **Affected versions:** Not version-specific

### `minio-init` must override `restart:`

- **Symptom:** The one-shot helper is restarted the moment it exits 0 and loops forever.
- **Cause:** `common/base.yaml` sets `restart: unless-stopped`, which every service
  inherits through `extends`.
- **Fix:** Keep `restart: "no"` on `minio-init`. It is the only sanctioned override of that
  key in the stack, and the reason `assert_config.py` compares `logging` rather than the
  whole shared fragment.
- **Affected versions:** Not version-specific

### `docker compose ps` shows `minio-init` as Exited

- **Symptom:** A container in the stack reads as stopped on a healthy run.
- **Cause:** It is a one-shot helper that has finished its work.
- **Fix:** Read exit code 0 as success. `wait-healthy.sh` already treats it that way.
- **Affected versions:** Not version-specific

### Buckets come from `MINIO_BUCKETS`, not from a seed directory

- **Symptom:** There is no `seed/` to add a bucket to, and a bucket added by hand
  disappears from the tracked configuration.
- **Cause:** `minio-init` creates buckets from the `MINIO_BUCKETS` variable.
- **Fix:** Add the name there and re-run `minio-init`; `mc mb --ignore-existing` makes that
  safe to repeat.
- **Affected versions:** Not version-specific

### The API port differs inside and outside

- **Symptom:** A container-to-container client configured with the host port cannot
  connect.
- **Cause:** The host sees `MINIO_API_PORT` (9100 by default) while in-network clients use
  the container port.
- **Fix:** Use `http://minio:9000` from inside the `devinfra` network and
  `localhost:${MINIO_API_PORT}` from the host.
- **Affected versions:** Not version-specific
