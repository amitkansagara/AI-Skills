# Central logging (Grafana + Loki + Alloy)

One Grafana URL where developers read the logs of every project and environment, without SSH or file
access on any application server.

- **Loki** stores logs. It is multi-tenant: **tenant = project**.
- **Grafana** is the dashboard. There is **one Grafana org per project**, and each org has a Loki data source
  pinned to that project's tenant. Developers are Viewers in their project orgs only.
- **nginx gateway** is the stack's only published port. It serves Grafana and a push-only Loki endpoint, with
  one credential per server. The tenant is derived from the credential.
- **Alloy** runs on each application server. It tails files, adds labels, redacts secrets, and pushes over HTTPS.

```
app server (Alloy, outbound only) --HTTPS + basic auth--> front proxy (TLS) --LAN--> gateway :BIND_PORT
browser ------------------------------HTTPS------------> front proxy        --LAN--> gateway
gateway --push, X-Scope-OrgID from login--> loki:3100   (internal network, auth_enabled)
gateway --UI--> grafana:3000 --one data source per org, fixed tenant header--> loki
```

Docs: [ONBOARDING](docs/ONBOARDING.md) (add a server or a project), [SECURITY](docs/SECURITY.md)
(access model, isolation test), [OPERATIONS](docs/OPERATIONS.md) (health, capacity, backup, upgrade,
troubleshooting).

## Layout

| Path | What |
|---|---|
| `docker-compose.yml` | loki, grafana, gateway; pinned tags, limits, restart policy, healthchecks |
| `.env.example` | every setting; copy to `.env` (0600, never committed) |
| `loki/loki-config.yml` | generic Loki config (retention, limits); no project names |
| `gateway/default.conf.template` | Host-based routing, push-only ingest, tenant map, IP allowlist |
| `grafana/provisioning/` | Platform data source, alert rules, dashboard providers; per-project files are generated |
| `dashboards/project/centralized-logs.json` | the one dashboard every project org gets |
| `dashboards/platform/platform-health.json` | alerts, disk, Loki size, silent forwarders |
| `alloy/lib/central.alloy` | shared Alloy module: redaction + authenticated push |
| `alloy/templates/` | per-source configs: odoo, python, nodejs, docker, nginx, postgresql, generic |
| `alloy/test/run.sh` | end-to-end test of the templates against a running stack |
| `scripts/logctl.py` | add/remove project and forwarder, healthcheck, isolation test |
| `scripts/backup.sh` | Grafana DB + credentials + config backup |
| `deploy/` | front-proxy vhosts, healthcheck systemd timer, Alloy resource limits |

## Versions (pinned 2026-11-26)

Loki 3.7.10, Grafana 13.3.1 (`grafana/grafana`, the OSS image; `grafana-oss` is no longer updated),
nginx 1.30.8-alpine, Alloy v1.22.0 on the application servers.

## Server specification

Stored size ≈ raw GB/day × retention days × ~0.15 (compression + index), plus 50% headroom.
One Foycom server writes about 10–15 MB of log per day.

| | Load | vCPU | RAM | Disk |
|---|---|---|---|---|
| Minimum | ≤10 servers, ≤1 GB/day, 7 days | 2 | 4 GB | 40 GB SSD, Loki on its own volume |
| Recommended | 20–50 servers, ≤5 GB/day, 14–30 days | 4 | 8 GB | 100–150 GB SSD data volume |
| Scalable | >20 GB/day or >30 days | 8 | 16–32 GB | chunks on S3/MinIO, Grafana DB on Postgres, Loki simple-scalable |

Network: inbound only from the front proxy to `BIND_ADDR:BIND_PORT`. Application servers need
**outbound** HTTPS to the ingest hostname and nothing inbound. The logging host needs outbound access only
for image pulls.

## Install (new logging host)

Requires Docker with the compose plugin, python3 and openssl. Run as root.

```bash
git clone <repo> /opt/central-logging && cd /opt/central-logging
chmod 750 /opt/central-logging
install -d -m 711 secrets                      # before the first "up", or Docker creates it as root 0755
cp .env.example .env && chmod 600 .env && $EDITOR .env
docker compose up -d
./scripts/logctl.py add-project <project>      # once per project
./scripts/logctl.py add-forwarder <project> <server>   # once per app server; prints its password once
cp deploy/central-logging-healthcheck.* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now central-logging-healthcheck.timer
```

Then pick an access mode (below) and onboard the servers ([ONBOARDING](docs/ONBOARDING.md)).

## Access modes

**HTTPS (normal).** DNS points `GRAFANA_HOST` and `INGEST_HOST` at the front proxy. Put
`deploy/front-proxy.conf` on that proxy (fill in the names and `UPSTREAM` = `BIND_ADDR:BIND_PORT`), run
certbot, and set `ALLOW_FROM` to the proxy's LAN IP. App servers use
`LOKI_URL=https://<INGEST_HOST>/loki/api/v1/push`, and people open `https://<GRAFANA_HOST>/`.

**LAN (no DNS / proxy yet).** Everything works over the private network, so you can test before DNS exists.

On the logging host, in `.env`, then run `docker compose up -d gateway grafana`:

```bash
INGEST_HOST=logs-ingest.example.com 192.0.2.10   # add the logging host's LAN IP
ALLOW_FROM=192.0.2.21; allow 192.0.2.22          # the app servers' LAN IPs
GRAFANA_URL=http://localhost:3000/
GF_COOKIE_SECURE=false
INGEST_URL=http://192.0.2.10:8300
```

On each app server, in `/etc/default/alloy`:

```bash
LOKI_URL="http://192.0.2.10:8300/loki/api/v1/push"
```

To open the dashboard, from any machine that can SSH to the logging host:

```bash
ssh -N -L 3000:127.0.0.1:3000 <user>@<logging-host>    # keep it running
# then browse http://localhost:3000 and log in with your Grafana user
```

The tunnel goes straight to Grafana's localhost port. Nothing new is opened on the LAN. For developers,
use an unprivileged account whose key can only forward that one port. In its `~/.ssh/authorized_keys`:
`restrict,port-forwarding,permitopen="127.0.0.1:3000",command="/bin/false" ssh-ed25519 AAAA...`

LAN mode pushes logs and the forwarder password as plain HTTP inside the private network, so use it only
on a trusted LAN and only until DNS exists. **Switching to HTTPS:** restore the HTTPS values above,
`docker compose up -d gateway grafana`, rotate each forwarder password
(`logctl.py add-forwarder <project> <server>` again), set the https `LOKI_URL` + the new password on each
app server, `systemctl restart alloy`, then run `logctl.py test-isolation`.

**Moving to another host:** install as above, restore the backup ([OPERATIONS](docs/OPERATIONS.md)), and point
the two DNS names (or the front proxy upstream) at the new host. Forwarders only know the hostname, so no
app server changes.

## Upgrade

1. `./scripts/backup.sh`
2. Read the release notes of every version between the old and new tag. For Loki, check schema/config
   deprecations; for Grafana, check provisioning and auth changes.
3. Change the tag in `.env`, then run `docker compose pull && docker compose up -d`.
4. Run `./scripts/logctl.py healthcheck` and `./scripts/logctl.py test-isolation`.

Alloy on app servers: `apt-get install alloy=<version>`, then `alloy validate /etc/alloy/conf.d`, then
`systemctl restart alloy`.
