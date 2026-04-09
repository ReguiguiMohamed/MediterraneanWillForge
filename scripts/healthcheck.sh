#!/usr/bin/env bash
# healthcheck.sh — verify that all core services are alive.
# Returns exit code 0 if all checks pass, 1 otherwise.
# Called after every `vagrant up` and `make deploy`.

set -euo pipefail

PASS=0
FAIL=0
ERRORS=()

check() {
  local name="$1"
  local url="$2"
  local expected_code="${3:-200}"

  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" 2>/dev/null || echo "000")
  if [ "$code" = "$expected_code" ]; then
    echo "  [OK]  $name  →  $url  (HTTP $code)"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $name  →  $url  (HTTP $code, expected $expected_code)"
    FAIL=$((FAIL + 1))
    ERRORS+=("$name")
  fi
}

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Mediterranean Ops Fortress — Health Check"
echo "═══════════════════════════════════════════════════════"
echo ""

check "MinIO API"         "http://localhost:9000/minio/health/live"
check "MinIO Console"     "http://localhost:9001"
check "Prometheus"        "http://localhost:9090/-/healthy"
check "Pushgateway"       "http://localhost:9091/-/healthy"
check "Grafana"           "http://localhost:3000/api/health"
check "Alertmanager"      "http://localhost:9093/-/healthy"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  echo "  FAILED services: ${ERRORS[*]}"
  echo "═══════════════════════════════════════════════════════"
  echo ""
  exit 1
fi

echo "  All systems operational."
echo "═══════════════════════════════════════════════════════"
echo ""
exit 0
