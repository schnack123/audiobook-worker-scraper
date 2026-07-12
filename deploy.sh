#!/usr/bin/env bash
# Build (linux/amd64), push to Docker Hub, trigger Sevalla deployment.
# Usage: ./deploy.sh [version]   (default version: vYYYYMMDD-HHMMSS)
set -euo pipefail
cd "$(dirname "$0")"

SERVICE="audiobook-worker-scraper"
IMAGE="mathiasschnack/${SERVICE}"
# Set once the Sevalla app exists (or export SEVALLA_APP_ID_OVERRIDE)
APP_ID="${SEVALLA_APP_ID_OVERRIDE:-}"
VERSION="${1:-v$(date +%Y%m%d-%H%M%S)}"

docker buildx build \
  --platform linux/amd64 \
  --build-context core=../audiobook-core \
  -t "$IMAGE:$VERSION" -t "$IMAGE:latest" \
  --push .

# Token lives in ../deploy.env (gitignored, shared by all service repos)
if [[ -z "${SEVALLA_API_TOKEN:-}" && -f ../deploy.env ]]; then
  source ../deploy.env
fi
if [[ -z "${APP_ID}" ]]; then
  echo "Image pushed as $IMAGE:$VERSION. APP_ID not set - deployment NOT triggered." >&2
  exit 1
fi
if [[ -z "${SEVALLA_API_TOKEN:-}" ]]; then
  echo "Image pushed as $IMAGE:$VERSION. SEVALLA_API_TOKEN not set - deployment NOT triggered." >&2
  exit 1
fi

curl -fsS -X POST "https://api.sevalla.com/v3/applications/${APP_ID}/deployments?company=${SEVALLA_COMPANY_ID}" \
  -H "Authorization: Bearer ${SEVALLA_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"docker_image\": \"${IMAGE}:${VERSION}\"}" >/dev/null

echo "Deployed ${IMAGE}:${VERSION} to Sevalla app ${APP_ID}"
