# MinIO — this Module's smoke checks.
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

dc minio mc alias set smoke "http://127.0.0.1:9000" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null 2>&1
BUCKET_LIST="$(dc minio mc ls smoke 2>&1)"
for bucket in ${MINIO_BUCKETS//,/ }; do
    assert_contains "bucket '${bucket}' provisioned" "${bucket}" "${BUCKET_LIST}"
done

FIRST_BUCKET="${MINIO_BUCKETS%%,*}"
assert_contains "object put/get round-trip" "smoke-ok" \
    "$(dc minio sh -c "echo smoke-ok > /tmp/smoke.txt && mc cp /tmp/smoke.txt smoke/${FIRST_BUCKET}/smoke.txt >/dev/null 2>&1 && mc cat smoke/${FIRST_BUCKET}/smoke.txt" 2>&1)"

assert_contains "versioning enabled on '${FIRST_BUCKET}'" "versioning is enabled" \
    "$(dc minio mc version info "smoke/${FIRST_BUCKET}" 2>&1)"
