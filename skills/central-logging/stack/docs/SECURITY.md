# Security

## Who can do what

| Who | Access |
|---|---|
| Platform admin | SSH to the logging host, Grafana server admin, `.env`, `secrets/` |
| Developer | Grafana login, **Viewer** in their project orgs. No SSH, no shell, nothing on app servers |
| App server (Alloy) | Push only, into its own project's tenant, with its own credential |
| Anyone else | Grafana login page only (via the front proxy) |

## Why tenants and not Grafana data-source permissions

Grafana OSS has no data-source permissions; that is an Enterprise/Cloud feature. Inside one org, **any
Viewer can send any query to any data source of that org** through `/api/ds/query`. So hiding a panel or
a variable value is not access control. The boundary here is:

1. **Loki tenant per project.** Loki runs with `auth_enabled: true` and every read or write needs
   `X-Scope-OrgID`. Loki itself is not published; only the gateway and Grafana reach it, over the compose
   network.
2. **Grafana org per project.** The org's only Loki data source sends a fixed `X-Scope-OrgID` from
   `secureJsonData` (encrypted, never returned by the API) and is provisioned (`readOnly`). A Viewer can
   query freely, but only that tenant. They cannot change the header, add a data source, or switch to an
   org they are not in.
3. **Platform org (id 1)** holds the health data and alerts. New users are auto-assigned there with role
   **None**, so they see nothing until an admin adds them to a project org.
4. **Ingest:** the gateway accepts only `POST /loki/api/v1/push`, with basic auth (SHA-512 crypt, a random
   32-byte password per server). It derives the tenant from the login `<project>--<server>` and overwrites any
   client-sent `X-Scope-OrgID`. A leaked server credential can only write into its own project, and only
   until it is removed. It cannot read anything.

`viewers_can_edit` is on: Viewers can use Explore and live tail, and change panels without saving. This does
not widen access, because of (1) and (2). Grafana marks the setting as deprecated; if it disappears in a
later Grafana, Viewers lose Explore but keep the dashboard.

Also set: sign-up and org creation off, anonymous off, external snapshots and public dashboards off,
secure cookies, no gravatar, no update checks or plugin preinstall from the internet.

## Transport

- App server → front proxy: HTTPS (Let's Encrypt), outbound only from the app server. Nothing listens on the
  app servers (the Alloy UI is bound to 127.0.0.1).
- Front proxy → logging host: plain HTTP on the private LAN, the same as the existing app vhosts. The gateway
  only accepts the front proxy's address (`ALLOW_FROM`). Upgrade path: a TLS listener on the gateway with an
  internal CA.
- Grafana is also published on `127.0.0.1:3000` for the admin scripts. Login is still required.

## On the app servers

- Alloy runs as the `alloy` system user. It has read ACLs on the configured log directories only, and is
  capped at 256 MB / 25% CPU by a systemd drop-in. The app's configuration is not changed.
- No Docker socket, except the opt-in `docker.alloy` template. The `docker` group is root-equivalent, so
  use it only where that is accepted. The alternative is the journald log driver.
- The credential is in `/etc/alloy/loki-password` (0640 root:alloy). It is not in the config or the
  environment.

## Redaction (before logs leave the server)

`alloy/lib/central.alloy` masks the values of password/secret/token/api_key/session_id/csrf_token/
authorization keys, Bearer/Basic credentials, `sk-…` API keys, OAuth `code=` parameters and email local
parts (the domain is kept). It is regex-based and best effort: **customer names, phone numbers, mail
subjects and IP addresses still pass.** Treat developer access to production logs as access to personal
data, and grant it accordingly.

## Isolation test

`./scripts/logctl.py test-isolation` (on the logging host, after any auth/proxy/Grafana change) creates a
temporary tenant, forwarder and Viewer. It then checks through the public URLs that:

- pushes without auth or with a wrong password get 401; query/admin paths on the ingest host get 404;
- a push with a spoofed `X-Scope-OrgID` lands in the credential's own tenant;
- a Viewer cannot see another tenant's canary, query another org or the Platform org, change or add a data
  source, read the tenant value, or save a dashboard;
- anonymous API calls get 401.

It removes everything it created and exits non-zero on any failure. Separately, check from another LAN host
that `curl http://<BIND_ADDR>:<BIND_PORT>/` gets 403/404, never Grafana.
