#!/usr/bin/env python3
"""Admin tool for the central logging stack. Run on the logging host, from anywhere.

  logctl.py add-project <project>            Grafana org + tenant-bound Loki data source + dashboard
  logctl.py add-forwarder <project> <server> push credential for one server (password printed once)
  logctl.py remove-forwarder <project> <server>
  logctl.py remove-project <project>         delete the org and its provisioning (Loki data expires)
  logctl.py healthcheck                      disk/budget/container/forwarder check, pushed to tenant "platform"
  logctl.py test-isolation                   end-to-end tenant isolation test through the public URLs

Standard library only. Needs: docker compose, openssl.
"""
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTPASSWD = os.path.join(ROOT, "secrets", "htpasswd")
PROV = os.path.join(ROOT, "grafana", "provisioning")
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # no "--": it separates project and server
PLATFORM_ORG = 1


def env():
    vals = {}
    with open(os.path.join(ROOT, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


E = env()


def compose(*args, **kw):
    return subprocess.run(["docker", "compose", "--project-directory", ROOT, *args], check=True, **kw)


def http(method, url, body=None, user=None, password=None, headers=None):
    """Return (status, parsed-json-or-text). Never raises on HTTP errors."""
    data = None
    h = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    if user is not None:
        h["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode(errors="replace")


def grafana(method, path, body=None, org=None, user=None, password=None):
    # Always name the org: without it Grafana uses the admin's last-used org from the browser.
    headers = {"X-Grafana-Org-Id": str(org or PLATFORM_ORG)}
    return http(method, f"http://127.0.0.1:{E.get('GRAFANA_LOCAL_PORT', '3000')}{path}", body,
                user or E["GF_ADMIN_USER"], password or E["GF_ADMIN_PASSWORD"], headers)


def check(cond, msg):
    if not cond:
        sys.exit(f"error: {msg}")


def org_id(project):
    status, data = grafana("GET", f"/api/orgs/name/{project}")
    return data["id"] if status == 200 else None


def reload_provisioning():
    for kind in ("datasources", "dashboards"):
        status, data = grafana("POST", f"/api/admin/provisioning/{kind}/reload")
        check(status == 200, f"reload {kind}: {status} {data}")


def add_project(project):
    check(NAME.match(project) and project != "platform", f"bad project name {project!r}")
    status, data = grafana("PUT", f"/api/orgs/{PLATFORM_ORG}", {"name": "Platform"})
    check(status == 200, f"rename platform org: {status} {data}")
    oid = org_id(project)
    if oid is None:
        status, data = grafana("POST", "/api/orgs", {"name": project})
        check(status == 200, f"create org: {status} {data}")
        oid = data["orgId"]
    # The admin is added to new orgs automatically; nobody else is.
    for kind in ("datasource", "dashboards"):
        tmpl = open(os.path.join(ROOT, "scripts", f"project-{kind}.yml.tmpl")).read()
        folder = "datasources" if kind == "datasource" else "dashboards"
        with open(os.path.join(PROV, folder, f"project-{project}.yml"), "w") as f:
            f.write(tmpl.format(project=project, org_id=oid))
    reload_provisioning()
    print(f"project {project}: Grafana org {oid}, tenant {project}")


def remove_project(project):
    check(project != "platform", "the platform org stays")
    for line in read_htpasswd():
        login = line.split(":", 1)[0]
        if login.startswith(project + "--"):
            remove_forwarder(*login.split("--", 1))
    for folder in ("datasources", "dashboards"):
        path = os.path.join(PROV, folder, f"project-{project}.yml")
        if os.path.exists(path):
            os.remove(path)
    oid = org_id(project)
    if oid:
        status, data = grafana("DELETE", f"/api/orgs/{oid}")
        check(status == 200, f"delete org: {status} {data}")
    print(f"removed project {project}")


def read_htpasswd():
    if not os.path.exists(HTPASSWD):
        return []
    with open(HTPASSWD) as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def write_htpasswd(lines):
    # nginx workers (uid 101 in the container) read the file per request: dir 0711, file 0644.
    # Host users are kept out by /opt/central-logging itself being 0750 root.
    os.makedirs(os.path.dirname(HTPASSWD), exist_ok=True)
    os.chmod(os.path.dirname(HTPASSWD), 0o711)
    tmp = HTPASSWD + ".tmp"
    with open(tmp, "w") as f:
        f.write("".join(line + "\n" for line in lines))
    os.chmod(tmp, 0o644)
    os.replace(tmp, HTPASSWD)
    compose("exec", "-T", "gateway", "nginx", "-s", "reload", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def add_forwarder(project, server):
    check(NAME.match(project) and NAME.match(server), "names: lowercase letters, digits, single dashes")
    check(os.path.exists(os.path.join(PROV, "datasources", f"project-{project}.yml")),
          f"run add-project {project} first")
    login = f"{project}--{server}"
    password = secrets.token_urlsafe(32)
    hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password, text=True,
                            capture_output=True, check=True).stdout.strip()
    lines = [line for line in read_htpasswd() if not line.startswith(login + ":")]
    write_htpasswd(lines + [f"{login}:{hashed}"])
    print(f"LOKI_USER={login}\nLOKI_PASSWORD={password}\n(shown once: put it in /etc/alloy/loki-password on {server})")


def remove_forwarder(project, server):
    login = f"{project}--{server}"
    lines = read_htpasswd()
    keep = [line for line in lines if not line.startswith(login + ":")]
    check(len(keep) < len(lines), f"{login} not found")
    write_htpasswd(keep)
    print(f"removed {login}")


def loki_count(oid, expr):
    """Instant LogQL count in a project org via Grafana's query API. None if no data."""
    body = {"queries": [{"refId": "A", "datasource": {"uid": "loki"}, "expr": expr, "queryType": "instant"}],
            "from": "now-1h", "to": "now"}
    status, data = grafana("POST", "/api/ds/query", body, org=oid)
    if status != 200:
        raise RuntimeError(f"query {expr}: {status} {data}")
    frames = data["results"]["A"].get("frames", [])
    total = 0
    for fr in frames:
        values = fr["data"]["values"]
        if values and values[-1]:
            total += sum(v for v in values[-1] if isinstance(v, (int, float)))
    return total if frames else None


def push_platform(record):
    """Push one JSON line to tenant "platform" from inside the gateway container (Loki is not published)."""
    stream = {"project": "platform", "environment": "production", "application": "central-logging",
              "server": socket.gethostname(), "service": "healthcheck", "level": "info"}
    payload = json.dumps({"streams": [{"stream": stream, "values": [[str(time.time_ns()), json.dumps(record)]]}]})
    compose("exec", "-T", "gateway", "wget", "-qO", "/dev/null", "--header", "Content-Type: application/json",
            "--header", "X-Scope-OrgID: platform", "--post-data", payload, "http://loki:3100/loki/api/v1/push")


def dir_bytes(path):
    total = 0
    def fail(err):
        raise err  # an unreadable volume must not be reported as 0 bytes

    for dirpath, _, files in os.walk(path, onerror=fail):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def healthcheck():
    rec = {}
    du = shutil.disk_usage("/")
    rec["disk_used_pct"] = round(du.used * 100 / du.total, 1)
    rec["disk_free_gb"] = round(du.free / 1e9, 2)

    mount = subprocess.run(["docker", "volume", "inspect", "central-logging_loki-data", "-f", "{{.Mountpoint}}"],
                           capture_output=True, text=True, check=True).stdout.strip()
    rec["loki_bytes"] = dir_bytes(mount)
    rec["over_budget"] = int(rec["loki_bytes"] > float(E.get("LOKI_BUDGET_GB", "5")) * 1e9)

    ps = compose("ps", "--all", "--format", "json", capture_output=True, text=True).stdout
    rows = [json.loads(line) for line in ps.splitlines() if line.strip()]
    bad = [r["Service"] for r in rows if r["State"] != "running" or r.get("Health") not in ("", "healthy")]
    # The Loki image has no shell for a compose healthcheck: ask its /ready from the gateway.
    ready = subprocess.run(["docker", "compose", "--project-directory", ROOT, "exec", "-T", "gateway",
                            "wget", "-qO", "/dev/null", "http://loki:3100/ready"], capture_output=True)
    if ready.returncode and "loki" not in bad:
        bad.append("loki")
    rec["unhealthy"] = len(bad) + max(0, 3 - len(rows))
    rec["unhealthy_services"] = bad

    # Soft budget safety valve: a full root disk would take the other services (app, Postgres) down with it.
    if du.free < float(E.get("VALVE_MIN_FREE_GB", "3")) * 1e9:
        print(f"VALVE: root free {rec['disk_free_gb']} GB, stopping loki", flush=True)
        compose("stop", "loki")
        sys.exit(2)  # no heartbeat now, so the heartbeat alert fires

    silent = []
    for line in read_htpasswd():
        login = line.split(":", 1)[0]
        project, server = login.split("--", 1)
        oid = org_id(project)
        n = loki_count(oid, f'sum(count_over_time({{server="{server}"}}[60m]))') if oid else None
        if not n:
            silent.append(f"{project}/{server}")
    rec["silent_servers"] = len(silent)
    rec["silent"] = silent

    push_platform(rec)
    print(json.dumps(rec))


# ---------------------------------------------------------------- isolation test

def test_isolation():
    """Creates tenant isolation-test, a forwarder and a Viewer user, attacks through the public URLs,
    then removes everything it created. Exit code 1 on any failed check."""
    ingest_base = E.get("INGEST_URL", f"https://{E['INGEST_HOST']}").rstrip("/")
    ingest = ingest_base + "/loki/api/v1/push"
    gurl = E["GRAFANA_URL"].rstrip("/")
    failures = []

    def expect(name, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    def push(user, password, line, extra=None):
        body = {"streams": [{"stream": {"project": "x", "environment": "test", "application": "isolation-test",
                                        "server": "isolation", "service": "canary"},
                             "values": [[str(time.time_ns()), line]]}]}
        return http("POST", ingest, body, user, password, extra)[0]

    def creds(project, server):
        out = subprocess.run([sys.executable, __file__, "add-forwarder", project, server],
                             capture_output=True, text=True, check=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    real = next(l.split(":")[0].split("--")[0] for l in read_htpasswd())  # an existing real project
    real_oid = org_id(real)
    canary = "ISOLATION-CANARY-" + secrets.token_hex(4)
    spoof = "SPOOF-CANARY-" + secrets.token_hex(4)
    user, upw = "isolation-dev", secrets.token_urlsafe(16)
    uid = None
    try:
        add_project("isolation-test")
        test_oid = org_id("isolation-test")
        c_test = creds("isolation-test", "isolation")
        c_real = creds(real, "isolation")

        expect("push with test creds accepted", push(c_test["LOKI_USER"], c_test["LOKI_PASSWORD"], canary) == 204)
        expect("push without auth refused (401)", http("POST", ingest, {"streams": []})[0] == 401)
        expect("push with wrong password refused (401)", push(c_test["LOKI_USER"], "wrong", "x") == 401)
        expect("spoofed X-Scope-OrgID push accepted into own tenant",
               push(c_real["LOKI_USER"], c_real["LOKI_PASSWORD"], spoof, {"X-Scope-OrgID": "isolation-test"}) == 204)
        for path in ("/loki/api/v1/query_range?query=%7Bproject%3D~%22.%2B%22%7D", "/loki/api/v1/labels",
                     "/ready", "/config", "/loki/api/v1/delete"):
            st = http("GET", ingest_base + path, user=c_test["LOKI_USER"],
                      password=c_test["LOKI_PASSWORD"])[0]
            expect(f"ingest host refuses {path.split('?')[0]} ({st})", st == 404)

        st, data = grafana("POST", "/api/admin/users", {"name": user, "login": user, "password": upw})
        check(st == 200, f"create user: {st} {data}")
        uid = data["id"]
        st, data = grafana("POST", f"/api/orgs/{real_oid}/users", {"loginOrEmail": user, "role": "Viewer"})
        check(st == 200, f"add viewer: {st} {data}")
        time.sleep(5)  # let Loki flush to queryable state

        def as_dev(method, path, body=None, org=None):
            h = {"X-Grafana-Org-Id": str(org)} if org else None
            return http(method, gurl + path, body, user, upw, h)

        def dev_query(expr, org):
            body = {"queries": [{"refId": "A", "datasource": {"uid": "loki"}, "expr": expr, "queryType": "range"}],
                    "from": "now-15m", "to": "now"}
            return as_dev("POST", "/api/ds/query", body, org)

        st, data = dev_query(f'{{application=~".+"}} |= "{canary}"', real_oid)
        frames = data.get("results", {}).get("A", {}).get("frames", []) if st == 200 else []
        rows = sum(len(f["data"]["values"][0]) for f in frames if f["data"]["values"])
        expect("viewer cannot see other tenant's canary in own org", st == 200 and rows == 0, f"{st} rows={rows}")
        st, data = dev_query(f'{{application=~".+"}} |= "{spoof}"', real_oid)
        frames = data.get("results", {}).get("A", {}).get("frames", []) if st == 200 else []
        rows = sum(len(f["data"]["values"][0]) for f in frames if f["data"]["values"])
        expect("spoofed push landed in the forwarder's own tenant", st == 200 and rows >= 1, f"{st} rows={rows}")
        st, _ = dev_query(f'{{application=~".+"}} |= "{canary}"', test_oid)
        expect(f"viewer cannot query another org's data source ({st})", st in (401, 403, 404))
        st, _ = dev_query('{service="healthcheck"}', PLATFORM_ORG)
        expect(f"viewer (role None in Platform) cannot query platform data ({st})", st in (401, 403, 404))
        st, data = as_dev("GET", "/api/user/orgs")
        names = sorted(o["name"] for o in data) if st == 200 else data
        expect("viewer sees only its project org (+ Platform with no role)",
               st == 200 and "isolation-test" not in names, str(names))
        st, _ = as_dev("POST", "/api/user/using/" + str(test_oid))
        expect(f"viewer cannot switch to another org ({st})", st in (401, 403))
        # Viewers may read data source metadata; the tenant header value must never be in it.
        st, data = as_dev("GET", "/api/datasources/uid/loki", org=real_oid)
        expect("data source API hides the tenant value from viewers",
               st in (200, 403) and "secureJsonData" not in str(data) and f"'{real}'" not in str(data), str(data)[:200])
        st, _ = as_dev("PUT", "/api/datasources/uid/loki", {"name": "Loki", "type": "loki", "access": "proxy",
                       "url": "http://loki:3100", "jsonData": {}}, org=real_oid)
        expect(f"viewer cannot change the data source ({st})", st == 403)
        st, _ = as_dev("POST", "/api/dashboards/db", {"dashboard": {"title": "x"}}, org=real_oid)
        expect(f"viewer cannot save dashboards ({st})", st == 403)
        st, _ = as_dev("POST", "/api/datasources",
                       {"name": "x", "type": "loki", "url": "http://loki:3100", "access": "proxy"}, org=real_oid)
        expect(f"viewer cannot add a data source ({st})", st == 403)
        st, _ = http("GET", gurl + "/api/search")
        expect(f"anonymous Grafana API refused ({st})", st == 401)
    finally:
        if uid:
            grafana("DELETE", f"/api/admin/users/{uid}")
        if any(l.startswith(f"{real}--isolation:") for l in read_htpasswd()):
            remove_forwarder(real, "isolation")
        remove_project("isolation-test")
        print(f"cleaned up (the canary lines in tenants isolation-test/{real} expire with retention)")
    print("ALL PASSED" if not failures else f"{len(failures)} FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    cmds = {"add-project": add_project, "add-forwarder": add_forwarder, "remove-forwarder": remove_forwarder,
            "remove-project": remove_project,
            "healthcheck": healthcheck, "test-isolation": test_isolation}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](*sys.argv[2:])
