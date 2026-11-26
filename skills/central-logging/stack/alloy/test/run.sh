#!/usr/bin/env bash
# End-to-end check of the real Alloy templates (00-central + odoo) against a running stack:
# multiline, level label, the 5 standard labels and redaction. Run on a host where the stack
# is up and .env is filled in, e.g. the local smoke test. Exit 1 on failure.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../.." && pwd)
set -a; . "$root/.env"; set +a
ALLOY_TAG=${ALLOY_TAG:-v1.22.0}
work=$(mktemp -d); trap 'rm -rf "$work"; "$root/scripts/logctl.py" remove-project alloy-test >/dev/null 2>&1 || true' EXIT
run=$(date +%s)

"$root/scripts/logctl.py" add-project alloy-test >/dev/null
"$root/scripts/logctl.py" add-forwarder alloy-test fixture > "$work/creds"
grep ^LOKI_PASSWORD= "$work/creds" | cut -d= -f2- > "$work/loki-password"

ts() { date -u -d "@$(( run - 60 + $1 ))" '+%Y-%m-%d %H:%M:%S,00'"$1"; }
cat > "$work/app.log" <<LOG
$(ts 1) 111 INFO foycom-staging werkzeug: run=$run GET /web?token=abc123SECRETTOKEN HTTP/1.1 200
$(ts 2) 111 WARNING foycom-staging foycom.addons.x: run=$run login failed for jane.doe@example.com password=Hunter2Secret!
$(ts 3) 111 ERROR foycom-staging foycom.addons.ai: run=$run key sk-or-v1-0123456789abcdef0123456789abcdef Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig
Traceback (most recent call last):
  File "x.py", line 1, in <module>
ValueError: boom
$(ts 4) 111 INFO foycom-staging foycom.addons.oauth: run=$run /auth/callback?code=4/0AbCdEfOAUTHCODE&state=xyz {'api_key': 'APIKEYVALUE123', "client_secret": "CLIENTSECRETVALUE"}
$(ts 5) 111 INFO foycom-staging foycom.addons.x: run=$run last line
LOG
mkdir "$work/conf.d"; cp "$root/alloy/templates/00-central.alloy" "$root/alloy/templates/odoo.alloy" "$work/conf.d/"
chmod -R a+rX "$work"

timeout 25 docker run --rm --network host --add-host "$INGEST_HOST:127.0.0.1" \
  -v "$root/alloy/lib:/etc/alloy/lib:ro" -v "$work/conf.d:/etc/alloy/conf.d:ro" \
  -v "$work/loki-password:/etc/alloy/loki-password:ro" -v "$work/app.log:/var/log/test/app.log:ro" \
  -e LOKI_URL="${INGEST_URL:-https://$INGEST_HOST}/loki/api/v1/push" -e LOKI_USER=alloy-test--fixture \
  -e PROJECT=alloy-test -e ENVIRONMENT=test -e APPLICATION=foycom-erp -e SERVER=fixture \
  -e ODOO_LOG=/var/log/test/app.log -e ODOO_SERVICE=foycom-server \
  "grafana/alloy:$ALLOY_TAG" run /etc/alloy/conf.d --storage.path=/tmp/alloy \
  --server.http.listen-addr=127.0.0.1:12399 > "$work/alloy.out" 2>&1 || true
grep -iE 'level=(error|warn)' "$work/alloy.out" | head -5 || true

python3 - "$root" "$run" <<'PY'
import sys, json
sys.path.insert(0, sys.argv[1] + "/scripts")
import logctl
oid = logctl.org_id("alloy-test")
body = {"queries": [{"refId": "A", "datasource": {"uid": "loki"}, "queryType": "range",
                     "expr": '{project="alloy-test"} |= "run=%s"' % sys.argv[2]}], "from": "now-15m", "to": "now"}
st, data = logctl.grafana("POST", "/api/ds/query", body, org=oid)
assert st == 200, (st, data)
entries = []
for fr in data["results"]["A"].get("frames", []):
    names = [f["name"] for f in fr["schema"]["fields"]]
    cols = dict(zip(names, fr["data"]["values"]))
    for labels, line in zip(cols["labels"], cols["Line"]):
        entries.append((labels, line))
text = "\n".join(line for _, line in entries)
fails = []
def ok(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    cond or fails.append(name)
ok("5 entries (traceback joined into its line)", len(entries) == 5)
ok("traceback in the ERROR entry", any("ValueError: boom" in l and " ERROR " in l for _, l in entries))
for secret in ["abc123SECRETTOKEN", "Hunter2Secret", "jane.doe", "0123456789abcdef0123456789abcdef",
               "eyJhbGciOiJIUzI1NiJ9", "OAUTHCODE", "APIKEYVALUE123", "CLIENTSECRETVALUE"]:
    ok(f"redacted {secret}", secret not in text)
ok("email domain kept", "***@example.com" in text)
levels = sorted({lb.get("level") for lb, _ in entries})
ok(f"levels {levels}", levels == ["error", "info", "warning"])
want = {"project": "alloy-test", "environment": "test", "application": "foycom-erp", "server": "fixture", "service": "foycom-server"}
ok("standard labels", all(all(lb.get(k) == v for k, v in want.items()) for lb, _ in entries))
if fails:
    print(text)
sys.exit(1 if fails else 0)
PY
