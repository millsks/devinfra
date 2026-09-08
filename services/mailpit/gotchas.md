# mailpit — gotchas

### `MP_DATABASE` must point into the named volume

- **Symptom:** Every message is lost on restart, with no error anywhere — the container
  still runs and the UI still answers.
- **Cause:** With `MP_DATABASE` unset, Mailpit keeps messages in memory. It is
  `/data/mailpit.db` here, and `mailpit-data` is mounted at `/data`.
- **Fix:** Keep `MP_DATABASE: /data/mailpit.db` and the `mailpit-data:/data` mount agreeing
  with each other in `services/mailpit/compose.yaml`.
- **Affected versions:** Not version-specific
- **Verified by:** `scripts/lint_selftest.py` — pins `MP_DATABASE` under the path
  `mailpit-data` is mounted at.

### SMTP authentication accepts anything, over plaintext

- **Symptom:** Any credentials are accepted, and an unencrypted session is accepted too.
- **Cause:** `MP_SMTP_AUTH_ACCEPT_ANY` and `MP_SMTP_AUTH_ALLOW_INSECURE` are both on, so an
  application can point at the sink without being configured for it.
- **Fix:** Local development only. Leave the published ports on `127.0.0.1` and never carry
  these settings anywhere else.
- **Affected versions:** Not version-specific

### `MP_MAX_MESSAGES: 5000` silently discards the oldest mail

- **Symptom:** A message that was there is no longer where you left it, with nothing
  reporting a deletion.
- **Cause:** The cap evicts the oldest messages once the store is full.
- **Fix:** Read a missing message as possibly aged out rather than never arrived, and raise
  `MP_MAX_MESSAGES` in `services/mailpit/compose.yaml` if you need a longer history.
- **Affected versions:** Not version-specific

### The smoke check speaks raw SMTP over bash's `/dev/tcp`

- **Symptom:** There is no Python SMTP client anywhere in the check, which looks like an
  omission.
- **Cause:** Speaking the protocol directly keeps the suite free of a Python dependency for
  this Module.
- **Fix:** Leave it. It counts messages before and after, so a Mailpit that accepted the
  session but stored nothing fails rather than passing.
- **Affected versions:** Not version-specific

### Two ports, two protocols

- **Symptom:** An application pointed at the wrong one opens a connection that then does
  nothing useful.
- **Cause:** `MAILPIT_SMTP_PORT` is SMTP; `MAILPIT_UI_PORT` is the web UI and the REST API.
- **Fix:** Send mail to `MAILPIT_SMTP_PORT` and read it from `MAILPIT_UI_PORT`; both are
  declared in `x-endpoints:` in `services/mailpit/compose.yaml`.
- **Affected versions:** Not version-specific
