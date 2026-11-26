---
name: central-logging
description: Set up, extend and operate our central logging platform (Grafana + Loki + Grafana Alloy on Docker Compose), so that developers read the logs of any server, project and environment in one Grafana without SSH or file access. Use whenever the user asks to set up central or centralized logging, a log dashboard, Grafana / Loki / Alloy / Promtail, to ship or forward logs from a server, onboard a new server or project to logging, give developers (read-only) log access, move the logging server, or find out why logs stopped arriving. Also covers the LAN mode used before DNS exists, and how to open the dashboard over the LAN (SSH tunnel).
---

# Central logging (Grafana + Loki + Alloy)

Everything needed is in [`stack/`](stack/). Copy that folder to `/opt/central-logging` on the logging
host. Its own docs go deeper:

- [`stack/README.md`](stack/README.md): install, access modes, sizing, upgrade.
- [`stack/docs/ONBOARDING.md`](stack/docs/ONBOARDING.md): add a project or a server, and the log-source templates.
- [`stack/docs/SECURITY.md`](stack/docs/SECURITY.md): access model and the isolation test.
- [`stack/docs/OPERATIONS.md`](stack/docs/OPERATIONS.md): health, alerts, capacity, backup, troubleshooting.

## How it works

```
app server: Alloy (user alloy) ─push, basic auth─▶ [front proxy, TLS] ─LAN─▶ gateway (nginx, only published port)
browser ─────────────────────────────────────────▶ [front proxy, TLS] ─LAN─▶ gateway ─▶ Grafana
gateway ─push, X-Scope-OrgID taken from the login─▶ Loki (auth_enabled, internal network only)
Grafana ─one org per project, one Loki data source with a fixed tenant header─▶ Loki
```

- **Tenant = project.** Each server's push login is `<project>--<server>`. The gateway derives the tenant
  from the login and overwrites any client header, so a server can only write into its own project.
- **One Grafana org per project.** Developers are **Viewer** in their project orgs only. New users land in
  the Platform org (id 1) with role *None* and see nothing until an admin adds them to a project.
- **Why not Grafana data-source permissions:** Grafana OSS has none (Enterprise only). Any Viewer can send
  any query to any data source in their org. Never rely on hiding panels or variables for isolation.
- **Labels on every stream:** `project, environment, application, server, service`, plus `level`
  (debug, info, warning, error, critical). Never use per-request or per-user values as labels.
- **Admin tool:** `scripts/logctl.py` handles `add-project`, `add-forwarder`, `remove-forwarder`,
  `remove-project`, `healthcheck` (5-minute systemd timer) and `test-isolation`.

## Rules

1. **Inspect before changing anything.** Use read-only commands first, and say what each change will do to
   the existing services.
2. **Ask before touching** a production app server, a shared front proxy, DNS, or any server you were not
   cleared for. Logging must never change or restart the application itself.
3. **Never** run `docker system/volume/image prune`, and never delete volumes, networks or containers that
   are not part of the `central-logging` compose project.
4. **Never publish Loki** (no `ports:` on it), and never let an unauthenticated endpoint reach it.
5. **No secrets in git:** not `.env`, `secrets/htpasswd`, or forwarder passwords. A forwarder password is
   printed once; pipe it straight into the app server's file (see below) instead of pasting it anywhere.
6. Keep the logging stack generic. Project names live only in Grafana orgs, logins and labels, never in
   `loki-config.yml`.

## 1. Collect the inputs

Ask the user, and never guess these:
- **Logging host:** its address, and whether it is dedicated or shared.
- **App servers:** the log files to ship on each, and project, environment and application names.
- **Access mode:** HTTPS (DNS names + a TLS front proxy), or **LAN mode** until DNS exists.
- **Admins:** who gets admin access, and which developers get access to which projects.

Then inspect, read-only:
- **Logging host:**
  `cat /etc/os-release; nproc; free -h; df -h /; docker version; docker compose version; ss -ltnp; docker ps; ls /opt`
- **Each app server:** where the log is and who owns it (`ls -la <dir>`), its rotation (`/etc/logrotate.d/*`),
  the timezone (`timedatectl`), and reachability of the logging host:
  `timeout 3 bash -c "</dev/tcp/<host>/<port>"`.
- **Size it:** daily log volume (file sizes / rotation period). Stored ≈ raw/day × days × 0.15, plus 50%
  headroom. One Odoo/Foycom server is about 10–15 MB/day. See the spec table in `stack/README.md`.

## 2. Install the stack (logging host, as root)

```bash
rsync -a <skill>/stack/ <host>:/opt/central-logging/      # or git clone; never copy a .env or secrets/
cd /opt/central-logging && chmod 750 . && install -d -m 711 secrets   # BEFORE first "up"
cp .env.example .env && chmod 600 .env
# fill in .env: a random GF_ADMIN_PASSWORD (python3 -c "import secrets;print(secrets.token_urlsafe(24))"),
# BIND_ADDR = the host's LAN IP, BIND_PORT = a free port, and the access-mode values (section 4)
docker compose pull && docker compose up -d
./scripts/logctl.py add-project <project>
cp deploy/central-logging-healthcheck.* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now central-logging-healthcheck.timer
./scripts/logctl.py healthcheck       # prints one JSON line; unhealthy must be 0
docker stats --no-stream | grep central-logging   # Grafana needs ~500 MB at start (limit 768m)
```

## 3. Onboard an app server (Alloy)

Follow `stack/docs/ONBOARDING.md` step by step. The points that matter:

```bash
# app server
install -d -m 755 /etc/apt/keyrings
wget -qO- https://apt.grafana.com/gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update && apt-get install -y alloy=<pinned>-1 acl && apt-mark hold alloy
systemctl stop alloy && systemctl disable alloy      # the package starts it with a sample config
gpasswd -d alloy adm; gpasswd -d alloy systemd-journal   # the package adds these: every log + whole journal
setfacl -m u:alloy:rx /var/log/<app-dir>             # only the log dir it needs
sudo -u alloy head -1 /var/log/<app-dir>/<file>      # must work; auth.log and app config must NOT
# copy stack/alloy/lib/central.alloy to /etc/alloy/lib/; 00-central.alloy + one template per source
# to /etc/alloy/conf.d/; alloy.env.example to /etc/default/alloy (fill in);
# deploy/alloy-limits.conf to /etc/systemd/system/alloy.service.d/limits.conf
mv /etc/alloy/config.alloy /etc/alloy/config.alloy.dist
```

Create the credential and pipe it across without printing it:

```bash
pw=$(ssh <logging-host> '/opt/central-logging/scripts/logctl.py add-forwarder <project> <server>' | sed -n 's/^LOKI_PASSWORD=//p')
printf '%s' "$pw" | ssh <app-server> 'install -m 640 -o root -g alloy /dev/stdin /etc/alloy/loki-password'; unset pw
```

Validate, then start. **Start only once the push endpoint answers.** On first start Alloy reads the
whole current file, and batches that fail too long are dropped, so starting against a dead endpoint loses
that history.

```bash
ssh <app-server> 'cd / && set -a && . /etc/default/alloy && set +a && sudo -E -u alloy alloy validate /etc/alloy/conf.d'
ssh <app-server> 'systemctl daemon-reload && systemctl enable --now alloy && journalctl -u alloy -n 30 --no-pager'
```

To follow the backlog, compare the position in `find /var/lib/alloy -name positions.yml` with the file
size.

## 4. Access modes

### HTTPS (normal)
- Admin: DNS `logs.<domain>` and `logs-ingest.<domain>` → the front proxy.
- Front proxy: `stack/deploy/front-proxy.conf` (fill in the names and `UPSTREAM=<BIND_ADDR>:<BIND_PORT>`),
  then `certbot --nginx -d ...`.
- `.env`: `GRAFANA_HOST`/`INGEST_HOST` = the names, `GRAFANA_URL=https://logs.<domain>/`,
  `GF_COOKIE_SECURE=true`, `ALLOW_FROM=<front proxy LAN IP>`.
- App servers: `LOKI_URL="https://logs-ingest.<domain>/loki/api/v1/push"`.
- To find the proxy's LAN IP if nobody knows it, start with `ALLOW_FROM=127.0.0.1` and read the
  `access forbidden by rule, client: <ip>` line in `docker compose logs gateway` after the first request.

### LAN mode (no DNS or front proxy yet)
Use this to test, or to run until an admin creates DNS. Logs and the push password cross the private LAN
as plain HTTP, so use it only on a trusted network, and **rotate the forwarder passwords when switching to
HTTPS.**

On the logging host's `.env` (keep a copy first: `cp .env .env.https`):

```bash
INGEST_HOST=logs-ingest.<domain> <logging-host-LAN-IP>   # the push arrives with Host = the IP
ALLOW_FROM=<app-server-1-IP>; allow <app-server-2-IP>     # the app servers (more: "; allow <ip>")
GRAFANA_URL=http://localhost:3000/                        # people come in through the SSH tunnel
GF_COOKIE_SECURE=false
INGEST_URL=http://<logging-host-LAN-IP>:<BIND_PORT>       # used by logctl.py test-isolation
```

Then `docker compose up -d gateway grafana`. On each app server, in `/etc/default/alloy`:
`LOKI_URL="http://<logging-host-LAN-IP>:<BIND_PORT>/loki/api/v1/push"`.

**Opening the dashboard over the LAN:** Grafana listens on the logging host's `127.0.0.1:3000`, which is
not reachable from the network. Tunnel to it from any machine that can SSH to the logging host:

```bash
ssh -N -L 3000:127.0.0.1:3000 <user>@<logging-host>   # keep this terminal open
```

Then browse **http://localhost:3000**:
- **Admin login:** user `admin`, password from `grep ^GF_ADMIN_PASSWORD /opt/central-logging/.env` on the
  logging host. Developers use their own Grafana users.
- **Logs:** org switcher (top left) → **<project>** → Dashboards → **Centralized logs**.
- **Health:** org **Platform** → **Platform health** (alerts, disk, Loki size, silent servers).
- If local port 3000 is busy, use `-L 3300:127.0.0.1:3000` and browse `:3300`. Links Grafana generates
  will still point at the `GRAFANA_URL` port.

For developers, never hand out a shell on the logging host. Give them an unprivileged user whose key can
only forward that one port. In that user's `~/.ssh/authorized_keys`:

```
restrict,port-forwarding,permitopen="127.0.0.1:3000",command="/bin/false" ssh-ed25519 AAAA... dev@laptop
```

They connect with `ssh -N -L 3000:127.0.0.1:3000 <that-user>@<logging-host>`.

**Switching LAN → HTTPS:** restore `.env.https` (or set the HTTPS values), then
`docker compose up -d gateway grafana`. Rerun `add-forwarder` for every server to rotate its password,
pipe the new one in, and set the https `LOKI_URL`. Then `systemctl restart alloy` on each server. Alloy
resumes from its positions, so nothing is sent twice. Finish with `logctl.py test-isolation`.

## 5. Verify (every setup, every change to auth, proxy or Grafana)

- **Logs arrive:** in the project org, run `sum(count_over_time({server="<server>"}[1h]))`. Check the
  labels, that the oldest line equals the file's start, that new lines appear within seconds, and that a
  traceback is one entry.
- **Redaction:** `{server="<s>"} |~ "(?i)password\\s*[:=]\\s*[^*\\s]"` and an email regex return nothing.
  Emails show as `***@domain`.
- **Templates:** `stack/alloy/test/run.sh` (needs a running stack and Docker). It pushes a fixture with
  fake secrets through the real templates and checks multiline, labels, levels and redaction.
- **Isolation:** `./scripts/logctl.py test-isolation` on the logging host must print `ALL PASSED`. It
  covers 401 without auth, 404 for query paths on the ingest host, spoofed tenant headers, Viewer limits,
  the Platform org and anonymous access.
- **From another LAN host:** `curl http://<BIND_ADDR>:<BIND_PORT>/` must give 403 or 404. Ports 3000 and
  3100 must refuse the connection.
- **Apps still healthy:** `systemctl is-active <app>` on each server. Alloy stays under 256 MB / 25% CPU.

## Gotchas we hit

- **Alloy package groups:** it adds `alloy` to `adm` and `systemd-journal`. Remove both after install and
  after every upgrade.
- **`alloy validate` from `/root`** panics with `stat .: permission denied`. Run it from `/`.
- **Comments in `/etc/default/alloy`:** never put a comment on a value line. systemd keeps it as part of
  the value (`LOKI_URL=...push# comment`).
- **`secrets/` created as root:** without `install -d -m 711 secrets` before `up`, Docker creates it as
  root 0755. The nginx workers (uid 101) need `x` on the directory and read on `htpasswd` (0644).
- **Allowlist source addresses:** a connection to the published port from the host itself, or via
  docker-proxy, arrives from the bridge gateway or the host's IP. `BIND_ADDR` is always allowed for this
  reason. Real LAN clients keep their own IP (iptables DNAT).
- **No shell in the Loki image,** so it has no compose healthcheck. `logctl.py healthcheck` checks
  `/ready` through the gateway. Loki answers 503 for about 15 s after a start.
- **Grafana 13 memory:** about 500 MB at idle, so a 512m limit gets OOM-killed. Use 768m.
- **The Grafana image is `grafana/grafana`;** `grafana/grafana-oss` stopped updating in 2026.
- **Admin API calls without `X-Grafana-Org-Id`** go to the admin's last org used in the browser.
  `logctl.py` always sends it.
- **Viewers can read data-source metadata** (`/api/datasources`). That is fine: the tenant value is
  `secureJsonFields` only and never returned. Test that it is not exposed, not that it returns 403.
- **LogQL on JSON health lines:** `| json | unwrap x` makes every field a label, giving one series per
  record and alerts that never clear. Use `| json v="x" | unwrap v` with `last_over_time`.
- **JSON in dashboards:** quotes inside LogQL must be `\"` in the dashboard JSON. Check with
  `python3 -m json.tool` before reloading, because a broken file just disappears from Grafana.
- **Rotation:** watch the current file only (`app.log`, not `app.log*`). With `copytruncate` rotation
  Alloy restarts at 0 by itself.
- **Loki 3 adds `service_name`** automatically. Turned off with `discover_service_name: []`; we set
  `service` ourselves. `loki.source.file` adds `filename`, which the shared module drops.
- **Docker log source:** the socket (`docker.alloy`) is root-equivalent. Prefer the journald log driver.
- **Shared hosts:** the healthcheck's safety valve stops Loki when the root disk has under
  `VALVE_MIN_FREE_GB` free, so logs never take down the app or Postgres. Free space, then run
  `docker compose start loki`.

## Moving the platform

Run `scripts/backup.sh` on the old host and install on the new one (section 2). Restore `config.tgz` and
`grafana-data.tgz` (see OPERATIONS). Then point the DNS names (or the proxy upstream) at the new host, and
the app servers need no change. In LAN mode, change `LOKI_URL` on every app server instead.
