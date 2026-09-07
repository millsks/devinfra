# mailpit — gotchas

- **`MP_DATABASE` must point into the named volume.** It is
  `/data/mailpit.db` and `mailpit-data` is mounted at `/data`. Unset it and
  Mailpit keeps messages in memory and loses every one on restart, with no error
  — the container still runs and the UI still answers.
- **`MP_SMTP_AUTH_ACCEPT_ANY` and `MP_SMTP_AUTH_ALLOW_INSECURE` are on.** Any
  credentials are accepted over plaintext. Local development only.
- **`MP_MAX_MESSAGES: 5000` silently discards the oldest mail.** A message that
  is not where you left it may have aged out rather than never arrived.
- **The smoke check speaks raw SMTP over bash's `/dev/tcp`** rather than using a
  Python client, so the suite needs no Python dependency. It counts messages
  before and after: a Mailpit that accepted the session but stored nothing fails
  it.
- **Two ports, two protocols.** `MAILPIT_SMTP_PORT` is SMTP, `MAILPIT_UI_PORT`
  is the UI and the REST API. Pointing an application at the UI port produces a
  connection that opens and then does nothing useful.
