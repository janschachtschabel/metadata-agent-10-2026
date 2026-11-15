#!/bin/sh
# Start a built image read-only and without any key, wait until it answers
# /health, check /ready and the internal port (probes, /metrics) and let it
# start Chromium once (audit OPS-03; /ready and the internal port since the
# audit of 14.11.). Used by both CI systems before an image is published.
#
#   scripts/smoke-image.sh <image> [host]
#
# host: where the published port is reachable - localhost on a GitHub runner,
# the service alias "docker" in a GitLab docker-in-docker job.
set -eu

image="$1"
host="${2:-localhost}"
name="metadata-agent-smoke"
ok=0

cleanup() {
  if [ "$ok" -ne 1 ]; then
    docker logs "$name" 2>&1 | tail -n 40 || true
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Read-only like the Helm chart (readOnlyRootFilesystem), with the same two
# writable directories and the internal port. Without keys the configuration
# is complete in mode forward and without the repository account - /ready must
# then answer 200.
docker run -d --name "$name" -p 8000:8000 \
  --read-only --tmpfs /tmp --tmpfs /home/appuser \
  -e METADATA_AGENT_B_API_AUTH_MODE=forward \
  -e METADATA_AGENT_REQUIRE_REPOSITORY=false \
  -e METADATA_AGENT_INTERNAL_PORT=9000 \
  "$image" >/dev/null

i=0
until wget -q -O - "http://$host:8000/health" | grep -q '"status"'; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "smoke test: $image did not answer /health within 60 s" >&2
    exit 1
  fi
  sleep 2
done

fail() {
  echo "smoke test: $image $1" >&2
  exit 1
}

# From inside the container: the internal port is not published.
inside() {
  docker exec "$name" python -c "import sys, urllib.request
sys.stdout.write(urllib.request.urlopen(sys.argv[1], timeout=10).read().decode())" "$1"
}

wget -q -O - "http://$host:8000/ready" | grep -q '"ready"' ||
  fail "is not ready on port 8000"
inside "http://localhost:9000/ready" | grep -q '"ready"' ||
  fail "is not ready on the internal port"
inside "http://localhost:9000/metrics" | grep -q 'metadata_agent_' ||
  fail "serves no metrics on the internal port"
# The init process of the image (Dockerfile, ENTRYPOINT).
[ "$(docker exec "$name" cat /proc/1/comm)" = "tini" ] ||
  fail "does not run tini as PID 1"

# Chromium is the one part that writes (profile, caches): it must start on the
# read-only filesystem, or screenshots fail in the cluster.
docker exec "$name" python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch(headless=True).close()
"

ok=1
echo "smoke test: $image answers /health, /ready and the internal port and starts Chromium read-only"
