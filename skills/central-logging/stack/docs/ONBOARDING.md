# Onboarding

## A new project

On the logging host:

```bash
cd /opt/central-logging
./scripts/logctl.py add-project <project>     # lowercase letters, digits, single dashes
```

This creates the Grafana org `<project>`, a Loki data source pinned to tenant `<project>`, and the
"Centralized logs" dashboard in that org. Then give developers access in Grafana (Administration → Users,
as server admin):

1. Create the user. Users cannot sign themselves up.
2. Open the user → Organizations → **Add user to organization** → `<project>`, role **Viewer**.

A user who is in no project org sees nothing. Never make developers Editor/Admin of an org or Grafana server
admin.

## A new server (7 steps)

Labels must be lowercase and stable. Never put request IDs, user IDs, emails or messages in labels.

| Label | Example | Meaning |
|---|---|---|
| `project` | `foycom` | product/client; also the tenant |
| `environment` | `production` | development, staging or production |
| `application` | `foycom-erp` | the app on that server |
| `server` | `app-prod-01` | the host name |
| `service` | `foycom-server` | the process / log file (set per template) |
| `level` | `error` | added by the template: debug, info, warning, error, critical |

**0. On the logging host:** `./scripts/logctl.py add-forwarder <project> <server>`. Copy the printed
password. It is shown once, and you can rerun the command to rotate it.

**1. Install Alloy** (Debian/Ubuntu, as root):

```bash
install -d -m 755 /etc/apt/keyrings
wget -qO- https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update && apt-get install -y alloy=1.22.0-1 acl
```

The package creates the system user `alloy` (no login shell) **and adds it to `adm` and `systemd-journal`**,
which can read every system log and the whole journal. Remove both (and again after every Alloy upgrade),
and keep the version pinned:

```bash
gpasswd -d alloy adm; gpasswd -d alloy systemd-journal; apt-mark hold alloy
systemctl stop alloy    # the package starts it with its sample config; start again in step 7
```

Run `alloy validate` as the alloy user from a readable directory (`cd /`); from `/root` it panics.

**2. Log source access:** give `alloy` read access to the log, and only that log:

```bash
setfacl -m u:alloy:rx /var/log/odoo                 # the directory
setfacl -m u:alloy:r  /var/log/odoo/odoo-server.log # only if the file is not already world-readable
setfacl -d -m u:alloy:r /var/log/odoo               # new files after rotation keep the access
```

**3–6. Config:** copy from this repo:

```bash
install -d -m 755 /etc/alloy/lib /etc/alloy/conf.d
cp alloy/lib/central.alloy /etc/alloy/lib/
cp alloy/templates/00-central.alloy /etc/alloy/conf.d/
cp alloy/templates/odoo.alloy /etc/alloy/conf.d/        # one file per log source, see below
cp alloy/templates/alloy.env.example /etc/default/alloy && $EDITOR /etc/default/alloy
#   PROJECT, ENVIRONMENT, APPLICATION, SERVER, LOKI_URL, LOKI_USER=<project>--<server>, source paths
install -m 640 -o root -g alloy /dev/null /etc/alloy/loki-password
$EDITOR /etc/alloy/loki-password                         # the password from step 0, nothing else
install -D -m 644 deploy/alloy-limits.conf /etc/systemd/system/alloy.service.d/limits.conf
alloy validate /etc/alloy/conf.d
```

**7. Start:**

```bash
systemctl daemon-reload && systemctl enable --now alloy && systemctl status alloy --no-pager
journalctl -u alloy -n 50 --no-pager    # look for "level=error" from loki.write
```

The logs show up in the project's dashboard within seconds. On first start the whole current file is
sent; Loki keeps what is inside retention.

**Remove a server:** `systemctl disable --now alloy` on it, then
`./scripts/logctl.py remove-forwarder <project> <server>` on the logging host.

## Log sources (templates)

| Template | Source | Settings in /etc/default/alloy | Read access |
|---|---|---|---|
| `odoo.alloy` | Odoo / Foycom server log, tracebacks joined | `ODOO_LOG`, `ODOO_SERVICE` | ACL on the log dir |
| `python.alloy` | Python `logging` text logs, tracebacks joined | `PYTHON_LOG`, `PYTHON_SERVICE` | ACL |
| `nodejs.alloy` | Node JSON logs (pino numeric levels, winston strings) | `NODE_LOG`, `NODE_SERVICE` | ACL |
| `nginx.alloy` | access (level from status) + error log | `NGINX_ACCESS_LOG`, `NGINX_ERROR_LOG` | ACL on `/var/log/nginx` |
| `postgresql.alloy` | Debian PostgreSQL log | `PG_LOG` | ACL on `/var/log/postgresql` |
| `generic.alloy` | any text log, level by keyword | `GENERIC_LOG`, `GENERIC_SERVICE` | ACL |
| `docker.alloy` | all containers via the Docker socket | none | `alloy` in group `docker` (root-equivalent, see SECURITY) |

Rules:

- Point at the **current file only** (`app.log`, not `app.log*`). Alloy follows rotation by itself, and a glob
  re-reads rotated copies under their new names.
- Two copies of the same template need different component labels (e.g. `loki.source.file "odoo_b"`),
  because all files in conf.d form one config.
- Prefer ACLs over adding `alloy` to `adm`: `adm` can read every system log (auth.log included).
- Apps that log only to journald: use `loki.source.journal` with a `matches` filter on the unit. That
  needs `alloy` in the `systemd-journal` group, which can read the whole journal.

After editing a template, run `alloy/test/run.sh` against a test stack. The Odoo template has a fixture
with fake secrets.
