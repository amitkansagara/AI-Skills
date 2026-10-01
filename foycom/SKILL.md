---
name: foycom
description: How Foycom (our ERP product, built from Odoo source) is developed, converted, deployed and operated. ALWAYS use whenever the user asks to deploy, ship, push live, update or restart anything on a server (e.g. "deploy this", "deploy on server", "pull on the server", "update staging"). Also use when working on Foycom modules or servers - upgrading modules, the Odoo-to-Foycom conversion (odoo_to_foycom.py, convert_odoo_to_foycom.sh, foycom-tools), foycom.conf / foycom.service / /usr/bin/foycom, module or migration version numbers (19.0 vs 9.0, 20.0 vs 10.0), building a Foycom .deb or Docker image, or when code written for Odoo behaves differently on a Foycom server.
---

# Foycom

Foycom is our ERP product. Its source is Odoo, run through the **foycom-tools converter**,
which renames the product, versions and images. We **write and test in Odoo**
(`odoo-bin`, `from odoo import ...`, `odoo.conf`), and every deploy turns that code into Foycom
code. Anything the converter does to your code happens on every deploy, so write code
that comes through it correctly.

## Server deployment flow (use for every "deploy on server" request)

**0. Confirm before deploying.** A deploy changes a live system. Before running
anything, confirm these with the user, and never guess any of them:
- the server;
- the repo in `/opt/odoo/`;
- the branch;
- the module(s) to upgrade, if any.

Then follow these steps in order:

1. **Pull in `/opt/odoo/<your-repo>`.** This is the clean Odoo-named checkout. Never
   pull in `/opt/foycom`.
   ```bash
   cd /opt/odoo/<your-repo> && git pull origin <branch> && git submodule update --init --recursive
   ```
2. **Run the convert script.** It regenerates all of `/opt/foycom` from `/opt/odoo`.
   ```bash
   /opt/convert_odoo_to_foycom.sh
   ```
3. **Upgrade the module, only when a restart isn't enough.** A restart reloads Python
   code only. If the change touches fields, views, XML data, security (ACLs or record
   rules), assets (JS, SCSS, templates) or the manifest version, the module must be
   upgraded, or the change never reaches the database:
   ```bash
   systemctl stop foycom
   cd / && sudo -u foycom /usr/bin/foycom --config /etc/foycom/foycom.conf -d <db> \
       -u <module1>,<module2> --stop-after-init --logfile /tmp/upgrade-<module>.log
   grep -E "ERROR|CRITICAL|Traceback|Running migration" /tmp/upgrade-<module>.log
   ```
   Run it from `cd /`, because from `/root` it fails on a docutils permission error.
   Fix any error before starting the service again. If you are not sure whether the
   change needs an upgrade, upgrade: it is safe for a Python-only change too.
4. **Restart the Foycom server.**
   ```bash
   systemctl restart foycom && systemctl status foycom --no-pager
   tail -n 100 /var/log/foycom/foycom-server.log      # no Traceback/ERROR on startup
   ```

The full recipe and its edge cases are in §6.

## 1. Versions: Foycom = Odoo − 10

| Odoo | Foycom | foycom-tools branch |
| --- | --- | --- |
| 16.0 | 6.0 | `6.0` |
| 17.0 | 7.0 | `7.0` |
| 18.0 | 8.0 | `8.0` |
| 19.0 | **9.0** (current) | `9.0` |
| 20.0 | 10.0 | `10.0` (to create) |
| 21.0 | 11.0 | `11.0` (to create) |

- A module version `19.0.1.62.0` arrives on the server as `9.0.1.62.0`. Under Odoo 20,
  `20.0.1.5.0` becomes `10.0.1.5.0`, and so on.
- `release.py` reports the Foycom major (9). `service/db.py` adds 10 back for the
  `server_version` RPC, so external clients still see the Odoo version.
- **Moving to a new Odoo series:** create the foycom-tools branch `N.0` and update
  three hardcoded spots in `foycom-developer-tools/odoo_to_foycom.py`:
  1. `version = "19.0"` → the new Odoo version (`version_minus_from_odoo = 10` stays).
  2. The migration-folder rename: `dirname.startswith("19.")` and `"9." + dirname[3:]`.
  3. The version regexes in `modify_manifest_py` and the migrations branch:
     `(['\"])19\.` → `\g<1>9.`.

  If you miss 2 or 3, Odoo 20 modules keep `20.0.*` versions while the core reports 10.
  The migrations then silently never run.

## 2. Where code lives

| Place | What | Rule |
| --- | --- | --- |
| Dev machine | Odoo source + our addon repos | Develop and test as plain Odoo |
| Server `/opt/odoo/<repo>` | Clean git checkouts of our addon repos, still Odoo-named | The **only** place to `git pull` |
| Server `/opt/foycom/<repo>` | Converted copy, on `addons_path` | **Generated. Never edit and never pull here: the next convert wipes it** |
| Server `/opt/foycom-tools` | That server's clone of foycom-tools (series branch) | The converter deploys actually run |
| Server `/opt/convert_odoo_to_foycom.sh` | `python3 /opt/foycom-tools/foycom-developer-tools/odoo_to_foycom.py /opt/odoo/ /opt/foycom/` | Converts **all** repos in one go |
| Core | `foycom` .deb → Python package `foycom` in dist-packages, `/usr/bin/foycom` | Built from converted Odoo, see §6 |

Different servers can carry different foycom-tools commits. Before trusting any rule
below on a given server, run `git -C /opt/foycom-tools log -1 --oneline`.

## 3. What the converter does

`odoo_to_foycom.py SRC DEST` (config in `config.ini` next to it):

1. **Wipes DEST** and copies SRC into it, `.git` included.
2. **Renames** every dir or file whose name contains a brand string, for example
   `sit-knowledge-retrieval-odoo` → `sit-knowledge-retrieval-foycom`.
3. **Rewrites content** in every text file, as plain substring replacement:
   `Odoo S.A.`/`OpenERP S.A.` → `Foycom`, `OpenERP` → `Foycom`, `Odoo` → `Foycom`,
   `odoo` → `foycom`, `ODOO` → `FOYCOM`. It also rewrites `Odoo 19.0` → `Foycom 9.0` and
   points app-store links to `#CommingSoon`.
   - Skipped: files with ignored extensions (png, jpg, svg, pdf, fonts, ico, webp, whl,
     zip, xlsx, ...) and `LICENSE`, `COPYRIGHT`, `README.md`, `CONTRIBUTING.md`.
4. **Versions:**
   - In `__manifest__.py`, a line containing `version` only gets the quote-led `19.` →
     `9.` rewrite. Brand replacement is **skipped on that line**.
   - Directories `migrations/19.*` → `migrations/9.*`. Inside files under
     `migrations/`, quote-led `'19.` / `"19.` → `'9.`.
   - Folders of other series (`18.0.x`) and series-less folders (`1.23.0`) are left
     alone.
5. **Images:** replaces Odoo logos and icons with the ones in `src/images` (the
   `[images]` list in `config.ini`).
6. **Kept as is:** the `DEFAULT_ENDPOINT` line in `website.py` and the
   `DEFAULT_LIBRARY_ENDPOINT`/`DEFAULT_OLG_ENDPOINT` lines in the `business_card`
   controllers.

Harmless noise: `utf-8 codec can't decode` (binary caches such as `.ruff_cache`) and
`Can not change image` when converting addons only (the core images are not there).

## 4. Writing code that survives conversion

- **Every `odoo` becomes `foycom`.** This covers imports, `odoo.define`, `@odoo/owl`
  and `@odoo/...` JS imports, `/odoo/...` web-client URLs, xml ids, comments, strings
  and module names. The core is converted the same way, so normal Odoo code keeps
  working.
- **Never hardcode a literal that must stay "odoo"**, such as:
  - external URLs (`*.odoo.com` APIs);
  - third-party package names (`odoorpc`, `odoo-test-helper`);
  - env var names;
  - JSON keys, routes or headers shared with an external frontend.

  On the server they silently become `foycom`. Keep such values in config or
  `ir.config_parameter`, or name them brand-neutrally.
- **External frontends** must not build `/odoo/...` URLs. The Foycom web client lives
  at `/foycom/...`. Use the API routes or ask the backend for the URL.
- **Manifest versions:** keep exactly one `19.0` at the start (`19.0.1.20.0`). Older
  converters rewrote every `19.0` in the string (`19.0.1.19.0` → `9.0.1.9.0` reads as
  a downgrade), and some servers may still run one. Skip such versions. Since the
  manifest's version line is not brand-replaced, never put `odoo` on that line.
- **Migration folders:** prefer **series-less** names (`migrations/1.23.0/`). Odoo
  compares only the module part, so the folder runs on every server, whichever
  converter that server has. `19.0.x.y.z` folders only work where the converter
  renames them. Either way, check for `Running migration [...]` in the upgrade log.
- **Tests and code that compare a version** against `"19.0..."` fail on the server,
  where the value is `9.0...`. Compare the module part, or read
  `ir.module.module.latest_version`.
- **Text inside binaries** (SVG, PDF, images) that says "Odoo" stays "Odoo". Make
  brand-neutral assets.
- **Preview before deploying:** convert locally and grep.
  ```bash
  python3 <foycom-tools>/foycom-developer-tools/odoo_to_foycom.py /path/to/parent_of_repo /tmp/foycom-preview
  grep -rIn "odoo" /tmp/foycom-preview --include=*.py --include=*.js --include=*.xml   # expect nothing
  grep -rn '"version"' /tmp/foycom-preview/*/*/__manifest__.py
  ls /tmp/foycom-preview/*/*/migrations
  ```
  Use the **server's** converter (copy `/opt/foycom-tools/foycom-developer-tools/`
  from the server), not a stale local branch. Never point DEST at a folder you care
  about, because it gets wiped.

## 5. Server layout and operations

- Service `foycom.service`: `ExecStart=/usr/bin/foycom --config /etc/foycom/foycom.conf --logfile /var/log/foycom/foycom-server.log`.
  It runs as user `foycom`, with the data dir in `/var/lib/foycom`. `odoo-bin` does not
  exist here, and `ps | grep odoo` finds nothing.
- `foycom.conf` keys that matter:
  - `addons_path`: core addons plus `/opt/foycom/<repo>` entries, never `/opt/odoo/`.
  - `db_name`: cron ignores `dbfilter`.
  - `workers`, `proxy_mode = True`, `server_wide_modules = web,queue_job` where
    queue_job is used.
  - `limit_time_real_cron = -1` for long AI cron jobs.
  - Secrets (admin password, db password, encryption keys) live only here, never in
    git or in logs you paste.
- Read-only SQL: `sudo -u foycom psql -d <db>`. For complex SQL, feed it on stdin from
  a file; inline `$$` quoting over ssh breaks.
- ORM shell: pipe a script into
  `cd / && sudo -u foycom /usr/bin/foycom shell --config /etc/foycom/foycom.conf -d <db> --no-http --log-level=error`.
  End the script with `env.cr.rollback()` unless you mean to write.
- Logs: `/var/log/foycom/foycom-server.log` and `journalctl -u foycom`.
- **Always `cd /` before running `/usr/bin/foycom` as `foycom`.** From `/root` it dies
  on a docutils `Permission denied: html4css1.css`.
- **Disk:** check `df -h /` before anything heavy. Docker builds, such as sidecar
  services, leave GBs of build cache. Run `docker builder prune -af` afterwards: a full
  disk takes Postgres down and Foycom with it.
- Run long jobs detached (`systemd-run`, `tmux`, `setsid`). `nohup` jobs died when the
  ssh session closed.

## 6. Deploy and upgrade

```bash
df -h /                                                     # room to work?
git -C /opt/foycom-tools log -1 --oneline                   # which converter
cd /opt/odoo/<repo> && git pull origin <branch> && git submodule update --init --recursive
/opt/convert_odoo_to_foycom.sh                              # regenerates ALL of /opt/foycom
systemctl stop foycom
cd / && sudo -u foycom /usr/bin/foycom --config /etc/foycom/foycom.conf -d <db> \
    -u <module1>,<module2> --stop-after-init --logfile /tmp/upgrade-<module>.log
grep -E "ERROR|CRITICAL|Traceback|Running migration" /tmp/upgrade-<module>.log
systemctl start foycom && tail -f /var/log/foycom/foycom-server.log
```

- **Restart only:** Python-only changes with no field, view, data, asset or manifest
  change.
- **Upgrade (`-u`):** new or changed fields, views, data/XML, security, assets (JS,
  SCSS, templates), manifest version bumps and migrations.
- **No Git credentials on the server?** Connect with `ssh -A` and pull over SSH with
  your forwarded agent. If the GitLab SSH port is non-standard, set `GIT_SSH_COMMAND`
  and use a `url.<ssh>.insteadOf` rewrite. Never store your credentials on the server.
- Convert runs over all of `/opt/odoo`, so other repos' uncommitted state there ships
  too. Keep `/opt/odoo` checkouts clean.
- **New repo on a server:** clone it into `/opt/odoo/`, convert, add
  `/opt/foycom/<repo-name-after-conversion>` to `addons_path`, restart, then `-i`
  the module.
- **Sidecar services** (Docker compose apps in a repo) run from the converted
  `/opt/foycom/<repo>` path. Keep their `.env` and override files in the `/opt/odoo`
  copy too, or the convert wipes them.

## 7. Building a Foycom core release (foycom-tools)

The foycom-tools repo has one branch per Foycom series (`6.0`, `7.0`, `8.0`, `9.0`, ...):

- `foycom-developer-tools/`: the converter, `config.ini` and the brand images in
  `src/images`.
- `foycom_debian/build_deb.sh -s <converted-foycom-source> [-a <addons>] [-i <auto-install-addons>] [-p <publish-dir>]`:
  copies extra addons into the source (`add_addons.py`; `-i` also sets
  `auto_install`, as do `base_setup` and `mail`). It then runs
  `setup/package.py --build-deb` and writes `build.log` in the source dir.
- `foycom_docker/`: Dockerfile that installs the built `foycom.deb`, verified against
  the SHA1 in `Packages`. Extra addons go in `/mnt/extra-addons`; the filestore is in
  `/var/lib/foycom`. The entrypoint waits for Postgres.
- CI (`.gitlab-ci.yml`, scheduled runs):
  1. Shallow-clone the Odoo branch and convert it.
  2. Commit the result to the foycom source repo's series branch.
  3. Build the .deb.
  4. Build the Docker image and push it as `foycom:<branch>` and
     `foycom:<branch>.<date>`.
  5. Smoke-test that `/web/login` returns 200.

  For a new series, update the CI branch names along with the converter edits in §1.

## 8. Gotchas checklist

- [ ] Edited or pulled in `/opt/foycom`? That change is lost on the next convert.
- [ ] Manifest version has a single leading `19.0` (or the current Odoo series).
- [ ] New migration folder is series-less, and `Running migration` appears in the log.
- [ ] No literal `odoo` that must survive: external URLs, packages, shared API keys.
- [ ] Ran the upgrade from `cd /` as user `foycom`, with the service stopped.
- [ ] `df -h /` checked, and Docker build cache pruned after builds.
- [ ] Used the server's converter commit (`/opt/foycom-tools`), not a guess.
- [ ] No secrets from `foycom.conf` pasted into chats, tickets or commits.
