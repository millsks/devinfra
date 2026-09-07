# Mailpit — this Module's smoke checks.
#
# Sourced by scripts/smoke-test.sh, never executed: the pass/fail counters are
# the driver's shell globals, and every value read here comes from the .env the
# driver loaded. There is no shebang and no `set` line for that reason.
#
# The driver has already established that this Module is running, so there is no
# `running` gate here.
#
# shellcheck shell=bash
# shellcheck disable=SC2154

BEFORE="$(curl -sf "http://${BIND}:${MAILPIT_UI_PORT}/api/v1/messages?limit=1" 2>/dev/null |
    sed -n 's/.*"messages_count":\([0-9]*\).*/\1/p')"
BEFORE="${BEFORE:-0}"

# Speak just enough SMTP over the raw socket to avoid a Python dependency.
if exec 3<>"/dev/tcp/${BIND}/${MAILPIT_SMTP_PORT}" 2>/dev/null; then
    {
        printf 'EHLO smoke\r\n'
        printf 'MAIL FROM:<smoke@example.com>\r\n'
        printf 'RCPT TO:<dev@example.com>\r\n'
        printf 'DATA\r\n'
        printf 'Subject: devinfra smoke test\r\n\r\nsmoke-ok\r\n.\r\n'
        printf 'QUIT\r\n'
        sleep 1
    } >&3
    cat <&3 >/dev/null 2>&1
    exec 3<&- 3>&-
    sleep 1

    AFTER="$(curl -sf "http://${BIND}:${MAILPIT_UI_PORT}/api/v1/messages?limit=1" 2>/dev/null |
        sed -n 's/.*"messages_count":\([0-9]*\).*/\1/p')"
    AFTER="${AFTER:-0}"
    if ((AFTER > BEFORE)); then
        pass "SMTP message accepted and stored (${BEFORE} -> ${AFTER})"
    else
        fail "SMTP message accepted and stored" "count did not increase (${BEFORE} -> ${AFTER})"
    fi
else
    fail "SMTP port ${MAILPIT_SMTP_PORT} reachable" "could not open socket"
fi
