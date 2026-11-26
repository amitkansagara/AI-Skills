#!/usr/bin/env bash
# Backup of what cannot be rebuilt from git: Grafana's database (users, orgs, roles),
# the forwarder credentials, the generated provisioning and .env. Loki data is NOT included
# by default (7 days of logs, the sources keep their own files); pass --with-logs to add it.
# Grafana is stopped for a few seconds so its SQLite file is consistent.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd); cd "$root"
NGINX_TAG=$(sed -n "s/^NGINX_TAG=//p" .env)  # reuse an image already on the host for tar
out="$root/backups/$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$out"; chmod 700 "$root/backups" "$out"

docker compose stop grafana
docker run --rm --entrypoint tar -v central-logging_grafana-data:/d:ro -v "$out":/b "nginx:$NGINX_TAG" czf /b/grafana-data.tgz -C /d .
docker compose start grafana

if [[ "${1:-}" == "--with-logs" ]]; then
  docker compose stop loki
  docker run --rm --entrypoint tar -v central-logging_loki-data:/d:ro -v "$out":/b "nginx:$NGINX_TAG" czf /b/loki-data.tgz -C /d .
  docker compose start loki
fi

tar czf "$out/config.tgz" .env secrets grafana/provisioning
chmod 600 "$out"/*
ls -la "$out"
