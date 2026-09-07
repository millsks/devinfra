# minio — gotchas

- **MinIO is archived; object storage runs Silo, a maintained fork.** Both
  `minio/minio` and `minio/mc` were archived upstream in 2026, and the final
  MinIO release — which fixed a privilege-escalation CVE — was never published to
  any registry, so the newest pullable MinIO image is permanently unpatched. Silo
  preserves the `MINIO_*` environment variables and the on-disk format, so this
  was an image swap with no data migration, and it is reversible: MinIO reads
  Silo-written data and vice versa (both directions verified). See
  `docs/adr/0008-object-storage-replacement.md`.
- **The volume is still named `minio-data`, and stays that way.** Renaming a
  volume orphans its data with no error anywhere (ADR 0004). The module directory
  is `services/minio/` for the same reason of least surprise; ADR 0004 freezes
  volume identifiers, not paths.
- **The same image supplies `mc`.** `minio-init` overrides the entrypoint to run
  the client, which is why there is no separate client image.
- **Volume size is not a data-integrity signal.** `.minio.sys` churns constantly
  through background healing. Check bucket and object listings instead.
- **`minio-init` must override `restart:`.** `common/base.yaml` sets
  `restart: unless-stopped`; without the `restart: "no"` override the one-shot
  helper is restarted the moment it exits 0 and loops forever. It is the only
  sanctioned override of that key in the stack, and the reason `assert_config.py`
  compares `logging` rather than the whole shared fragment.
- **`docker compose ps` shows `minio-init` as Exited.** That is success, not a
  crash; `wait-healthy.sh` treats an exit code of 0 as done.
- **Buckets come from `MINIO_BUCKETS`, not from a seed directory.** Add one there
  and re-run `minio-init`; `mc mb --ignore-existing` makes that safe to repeat.
- **The API port differs inside and outside.** The host sees
  `MINIO_API_PORT` (9100 by default) while in-network clients use
  `http://minio:9000`.
