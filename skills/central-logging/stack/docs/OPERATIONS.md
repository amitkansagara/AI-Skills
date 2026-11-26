# Operations

## Health and "never silently stop"

- **Restart policy:** all three containers use `unless-stopped`. Grafana and the gateway have Docker
  healthchecks. The Loki image has no shell, so its `/ready` is checked by the healthcheck below.
- **Healthcheck timer** (`central-logging-healthcheck.timer`, every 5 min, runs
  `scripts/logctl.py healthcheck` as root). It records:
  root disk %, free GB, Loki volume bytes, `over_budget` (volume > `LOKI_BUDGET_GB`), unhealthy containers
  (including Loki `/ready`), and **silent forwarders**: every credential in `secrets/htpasswd` whose server
  sent nothing in 60 minutes. It pushes the record to tenant `platform` and prints it to the journal.
- **Alerts** (Platform org → Alerting, and the Platform health dashboard):

| Rule | Fires when |
|---|---|
| Healthcheck heartbeat missing | no record for 15 min: host, Docker, Loki or timer down, or the valve fired |
| Root disk above 80% | |
| Loki data above its budget | volume > `LOKI_BUDGET_GB` |
| A logging container is not healthy | for 5 min |
| A forwarder sent no logs for 60 minutes | the record's `silent` list says which |

There is no contact point yet: alerts are visible in the Grafana UI only. To add one, go to Alerting →
Contact points (email needs `GF_SMTP_*` in compose; Google Chat/Slack need a webhook), then set it in the
default notification policy.

- **Safety valve (soft budget):** if the root disk has less than `VALVE_MIN_FREE_GB` free, the healthcheck
  stops Loki, so that logs cannot fill the disk under the other services on the host. The heartbeat alert
  then fires. Free space, then run `docker compose start loki`. Alloy retries for a few minutes and then drops
  batches. The source files stay on the app servers, so a gap can be re-sent (see Troubleshooting).

## Capacity and retention

- Retention: `limits_config.retention_period` (168h), applied by the compactor every 10 min plus a
  `retention_delete_delay` of 2h. `reject_old_samples_max_age` and `max_query_lookback` match it. When you
  change retention, change all three, then `docker compose up -d loki`.
- Measure ingest per server: in a project org, query
  `sum by (server) (bytes_over_time({project="<p>"}[1d]))`. The dashboard has an "Ingested bytes per server"
  panel. Stored size: the "Loki data size" panel (Platform).
- Rule of thumb: stored ≈ raw × days × 0.15. When the Loki volume trends above about 70% of the budget:
  lower retention, cut noisy sources (e.g. debug level, health-check request lines, via `stage.drop` in the
  template), or move to a bigger data volume.
- Per-tenant limits (`ingestion_rate_mb` 4, burst 8, 3 MB/s per stream, 256 KB lines truncated,
  5000 streams) stop one runaway server from flooding the store. Pushes over the limit get 429, which
  shows up in that server's `journalctl -u alloy`.

## Backup and restore

`./scripts/backup.sh` writes `backups/<UTC time>/`, readable by root only:

- `grafana-data.tgz`: users, orgs, roles, alert state. Grafana is stopped for a few seconds while it runs.
- `config.tgz`: `.env`, `secrets/` (forwarder hashes), generated provisioning.
- `loki-data.tgz` only with `--with-logs`. Logs are short-lived and the sources keep their own files.

Copy the folder off the host (it contains secrets). Restore on a fresh install:

```bash
cd /opt/central-logging && tar xzf <backup>/config.tgz
docker compose up -d && docker compose stop grafana
docker run --rm --entrypoint sh -v central-logging_grafana-data:/d -v <backup>:/b nginx:<NGINX_TAG> \
  -c 'rm -rf /d/* && tar xzf /b/grafana-data.tgz -C /d && chown -R 472:0 /d'
docker compose start grafana
./scripts/logctl.py healthcheck && ./scripts/logctl.py test-isolation
```

## Troubleshooting

| Symptom | Check |
|---|---|
| No logs from a server | on it: `systemctl status alloy`, `journalctl -u alloy -n 100` (401 = wrong password/login, 403 = proxy/gateway allowlist, 429 = rate limit, 400 "too old" = older than retention). `curl -sI https://<ingest-host>/loki/api/v1/push` should give 405/401, not a timeout |
| Alloy cannot read the file | `sudo -u alloy head -1 <file>`; fix the ACL (step 2 of ONBOARDING) |
| Dashboard empty but data exists | variables set to All? time range? Is the user a Viewer in that project's org, not just Platform? |
| Grafana 502 via proxy | `docker compose ps`; `docker compose logs grafana gateway` |
| Loki not ready | `docker compose logs loki`; after a start it needs about 15 s |
| Disk filling | Platform dashboard; `docker system df -v`; lower retention; the valve may have stopped Loki |
| Re-send a gap | on the server: stop alloy, remove the file's entry from `/var/lib/alloy/data/loki.source.file.*/positions.yml` (it re-reads the whole current file; Loki drops exact duplicates), start alloy |

Never run `docker system prune` or `docker volume prune` on a shared host: other stacks' volumes live there too.
