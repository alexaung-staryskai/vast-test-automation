# VAST Test Bench

## Quick start

**You need:** Python 3.10 or newer, internet access for the first start, and the customer VPN to reach the VMS and the test clients.

| | Start | Stop |
|---|---|---|
| **Windows** | Double-click **`run.bat`**, or in cmd or PowerShell: `.\run.bat` | Double-click **`stop.bat`**, or Ctrl+C in the run window |
| **macOS** | Double-click **`run.command`** in Finder, or in Terminal: `./run.sh` | Double-click **`stop.command`**, or `./stop.sh`, or Ctrl+C |
| **Linux** | `./run.sh` | `./stop.sh`, or Ctrl+C |

The app opens in your browser at **http://127.0.0.1:8080**. The first screen asks you to **create a test site** or open an existing one.

- **First start:** it takes a minute or two. The script creates a Python environment in `.venv` and installs the required packages. Later starts take a few seconds, and the packages are only installed again when `requirements.txt` changes.
- **Already running?** Running the start script again opens the browser on the running app.
- **Open a test site directly:** `./run.sh sites/acme-poc` (Windows: `run.bat sites\acme-poc`).
- **Other options:** `--port 8081` uses another port (the default is `$PORT`, then the site's `app.port`, then 8080). `--no-browser` doesn't open the browser. `stop --port 8081` stops only that instance.
- **No Python?** Install it from https://www.python.org/downloads/. On Windows, tick **"Add python.exe to PATH"**. On macOS you can also run `brew install python`.
- **Ubuntu/Debian without `python3-venv`:** it works without sudo. Ubuntu first prints *"ensurepip is not available … apt install python3.12-venv"*; ignore that. The script then installs pip into `.venv` by itself.
- **Behind a proxy:** set `HTTPS_PROXY` before the first start, because the packages come from PyPI.
- **What the scripts do:** `run.bat`, `run.sh` and `run.command` find Python 3.10+ and call `start.py`, which does the setup and starts the app. `stop.*` calls `start.py --stop`.
- **Security:** the app only listens on 127.0.0.1, because it holds credentials and shell access.

## What it does

Local web app (runs on your laptop, over the customer VPN) that automates:

1. **Tenant onboarding + API tests** (VMS REST API): VIP-range check → tenant → admin role → tenant admin → VIP pool → quota → QoS → S3 user + keys → view policy → view. Every step is verified with a GET. Same order and payloads as VAST's tenant onboarding web application (validated on VMS 5.5). On each tenant's card, switches lift and restore its **QoS and quota limits** on the VMS.
2. **Clients**: SSH consoles in the browser (add as many as needed), required-package check/install/update (including the **VAST NFS driver**), NFSv3 mount (multipath `remoteports` over TCP or RDMA), read-ahead.
3. **Performance**: elbencho Write / Read / Auto Write/Read presets, configurable in the `elbencho:` config section. Results are parsed and summed across clients, shown in MB/s, GB/s or TB/s, and exported to JSON, TXT and CSV.
4. **vperfcheck**: VAST's profile-driven elbencho wrapper, run from one client against all of them together (file system and S3), with the passwordless SSH it needs set up and tested by the app. See [vperfcheck](#vperfcheck).

### Test sites

Each test site is one folder. Everything for that test is saved inside it:

```
sites/<site-name>/
  site.yaml        name, description, created date
  config.yaml      VMS connection, variables, VIP plan, clients, mount, packages, presets
  tenants.yaml     onboarded tenants
  .tenant-secrets.json  admin passwords + S3 secret keys per tenant (mode 600, hidden, not in the zip)
  results/         exported runs (JSON + TXT)
  config-backups/  previous versions of config.yaml
```

- **Create** starts from one of three things. Tenants and results are never copied.
  - **Example configuration.**
  - **A template from `templates/`.** For example **Lab example (VAST 5.5)** (`templates/lab-example.yaml`):
    - `https://vms.example.com`, `/api/v8`, Api-Token auth, SSL verified;
    - VIP pool 10.20.0.10–250, /16, 5 IPs.

    Change the VMS URL and VIP ranges to your cluster's. Templates for your own clusters can be named `templates/local-*.yaml`: they show up in the app but are git-ignored, so internal hostnames stay on your machine.

    Run `export VAST_API_TOKEN=...` before starting, which is the same variable the Tenant onboarding app uses, or paste the token in Tab 1.
  - **A copy of another site's configuration.**

  To add your own template, drop a config file into `templates/`. `# label:` and `# description:` lines on its first lines set how it's listed.
- **Hand over:** click 📁 *site name* → **⤓ Download site (.zip)**, or just copy the folder. The zip leaves out backups and the hidden run state that holds generated secrets.
- **Replicate:** the receiver unzips the folder and opens it with **Open from a folder** in the app, or starts the app directly on it:

  ```bash
  ./run.sh /path/to/ACME-POC-Nov-2026
  ```

- The site name in the header switches between sites. Switching closes the open SSH consoles.
- The 📁 Test site tab lists the sites in two tabs: **Active sites** (each row has **Open site** and **Delete site**) and **VMS Record Deleted Sites** (sites whose tenant was deleted from the VMS; kept for their test results and still openable). The count is shown on each tab and the last tab used is remembered. **🗒 Delete log** and **＋ New Test Site** are at the top right.
- **Delete site** opens a 4-step window: **① Confirm names** - type the site name and the tenant name on the VMS (the button stays off until both match) → **② Review VMS assets** - the app finds the tenant on the site's VMS and lists everything under it → **③ Delete** - removes them one by one, animated, in this order: view → view policy → S3 user → QoS policy → quota → VIP pool → tenant admin → admin role → tenant → local provider, waiting until each is really gone. The **local provider** (`provider-<tenant>`, holds the tenant's users) is not removed by the VMS together with the tenant, and the VMS refuses to delete it while the tenant exists, so the app deletes it as the last step, right after the tenant. If the tenant is already gone but its `provider-<tenant>` was left behind (and no tenant uses it), the review lists it and deletes it. For a site already in **VMS Record Deleted Sites**, **🧹 Check VMS for leftovers** runs the same check and deletes what was left behind → **④ Done** - the site is **kept** with its configuration, `tenants.yaml` and test results; the site and its tenant are marked **VMS record deleted** (the site row shows a tag instead of Delete site; the tenant shows that status in 🏢 Tenants). If a delete fails, it stops there, keeps the site and offers **Scan again and continue**. If the VMS refuses to delete the tenant (e.g. *Failed to delete tenant's empty subdirectories … consider using "force"*), **⚠ Force delete tenant…** appears: type `force delete <tenant name>` and the tenant is deleted with `force_remove=true`, which also deletes any files left in its directory. It is only allowed once nothing else is left under the tenant. Every deletion step, failure and force delete is recorded in **🗒 Delete log** on the 📁 Test site tab (`sites/.delete-log.jsonl`). Every object is checked against its own tenant before it is deleted; roles shared with other tenants, the default tenant and anything not provably the tenant's are left alone. A tenant that isn't in the site's records needs an extra tick. If the VMS can't be reached, nothing changes and you can try again. Only folders directly inside `sites/` can be deleted from the app.

Secrets can stay out of the file: `api_token: "${env:VMS_API_TOKEN}"`, `password: "${env:SSH_PASSWORD}"`.

```bash
export VMS_API_TOKEN=... SSH_PASSWORD=...
```

## Tabs

| Tab | What it does |
| --- | --- |
| 📁 Test site | Current site details (folder, created, VMS, contents), edit name/description, download zip, replicate instructions, create / open / switch sites. Only tab available until a site is open |
| ① Onboarding & API tests | VMS connection (editable), onboarding settings grouped in boxes - Tenant, Users, VIP Pool (incl. number of IPs / CIDR / VLAN), Quota, QoS, View Policy, View - each showing its step status; **💾 Save as site defaults** writes them to the site's config.yaml (`${env:…}` references are kept), VIP-pool IP map showing every /24 the candidate ranges touch (.0–.255, labelled rows; IPs outside the candidate ranges are outlined and can't be picked; click a free IP to choose where the block starts, or type a **Start IP – End IP** range; the VIP Pool box lists the VIP IPs - the chosen range before step 1, the pool's live range on the VMS after it; once the pool exists, **🗺 Change VIP range…** changes its range on the VMS with `PATCH /vippools/{id}/`, after checking no other VIP pool or compute cluster holds the new IPs), **Create Tenant** (runs all steps) / single step, Credentials (generated admin password + S3 keys), Export report, Cleanup (reverse order; objects that were reused are left in place) |
| 🏢 Tenants | Summary card per onboarded tenant (IDs, admin + login URL, VIP range, view/NFS export + mount example, S3 endpoint/bucket/access key, QoS, quota), loaded from `tenants.yaml`. The **admin password** and **S3 secret key** are masked: 👁 shows, ⧉ copies. They come from `.tenant-secrets.json`, so they survive *Reset state* and later runs. Opens automatically on start-up when tenants exist. **✓ Verify on VMS** opens a window that checks, live and step by step, that the VMS is reachable and every object onboarding created still exists (read-only); Cleanup marks a tenant *deleted* (kept as history) |
| ② Clients · Mount · Performance | Saved clients list (custom hosts are saved to `clients:`, passwords to a hidden `.client-secrets.json`) with **✎ Edit** (name, host, port, user, password - test before saving) and **🔌 Test** (step by step: host name → SSH port and banner → login → a command → sudo; read-only), consoles with **⏏ Disconnect / ↻ Reconnect** (and **⏏ Disconnect all**), **⤓ .md** export and **⤢** large view, **Check packages / Install missing / Update / Check VAST driver version**, **Presets** grouped in dropdowns (**Client Cmds**: Pre-checks, Client info · **Connection**: Ping VMS with VIP · **File System**: Mount NFS, Mount Status, Set Read-ahead, Unmount · **Performance Test**: Elbencho Write / Read / Auto Write/Read · **Utilities**: Delete Elbencho Files, Clear Linux Cache · **Customs**: your own Linux commands from ⚙ Configuration → Custom Linux commands, which can be exported to / imported from a YAML file to reuse them on other sites), result summary and exported files |
| 📈 Chart | Performance across test sessions. **Timeline**: a histogram over clock time (even time buckets; each column is the time-weighted average of the elbencho phases that ran in it, Write and Read side by side). **Compare sessions**: grouped bars per session. Session list on the right shows/hides sessions ("only" isolates one; the timeline zooms to what's visible); legend toggles Write/Read; throughput in MB/s · GB/s · TB/s or IOPS; hover for values per client; table view |
| 🗒 Logs | Reading-pane view of every run log (package checks, installs, mounts, presets, onboarding reports, console exports): list on the left (filter by type/status, search, ↑/↓), the selected file on the right (txt / json, copy, download). **Archive logs** zips them into `results/archive/`; **Purge logs** deletes them. Performance (elbencho) results are left alone unless ticked |
| ⚙ Configuration | Overview cards, form editor, raw YAML editor, validation. Saves keep comments; each save backs up the previous file to `config-backups/` |

Theme: the ◐ button in the header cycles through Auto, Light and Dark.

## Consoles survive page reloads

SSH consoles live on the server, not in the page. Reloading or closing the page only detaches it, and the page shows every open console again on load: with its output (the last ~2 MB is kept on the server), status, ✓ selection and any run still in progress. Several pages can show the same console. **⏏ Disconnect** closes the SSH connection but keeps the console and its output; **✕** closes it for good. A console that no page has shown for `ssh.idle_close_minutes` (default 60, 0 = never) is closed automatically, unless a preset is running on it. Restarting the server ends all consoles.

## Interactive prompts

Preset and package commands run on a PTY. When a command prints something that looks like a question (`execution.input_prompt_regex`) and then goes quiet for `input_idle_seconds`, the console is highlighted with **⌨ input needed**. The run status also shows it. Type the answer in that console; keystrokes go to the running command.

`sudo` passwords are answered automatically from the SSH password, and that password is masked in the output and in the exports.

## Key config sections

- `vms`: the VMS connection, which you can also edit and test in Tab 1. The auth token field is masked. Save writes it to `config.yaml`, which is in `.gitignore`. To keep the token out of the file, enter `${env:VMS_API_TOKEN}` instead.
- QoS and quota take units, chosen from drop-downs in the UI:
  - QoS: `qos_read_limit: 500 MB/s` or `2 GB/s`. The VMS receives `*_mbps`, with 1 GB/s = 1000 MB/s and a minimum of 100 MB/s.
  - Quota: `quota_hard_limit: 10 TB` (MB, GB or TB). The VMS receives `*_bytes`, with 1 TB = 10^12 bytes.
- `app.tenants_file`: where onboarded tenants are kept (default `tenants.yaml`). The admin password and S3 secret key are only stored there when `app.store_secrets: true`.

- VIP pool settings in Tab 1 (stored as `vip_pool.ip_count`, `subnet_cidr` and `vlan`):
  - **Number of IPs:** default 5, minimum 5.
  - **Subnet CIDR:** default /16.
  - **VLAN:** default 0, meaning none.

  A change applies to the current session. **Save as site default** writes it to the site's `config.yaml`. The values each VIP pool was created with are recorded in `tenants.yaml`.
- `vip_pool`: the candidate `ranges`, plus `ip_count` (minimum 5), subnet, gateway, VLAN and role. The check treats every VIP pool and compute-cluster IP (including the kube VIP) as used, and you can ping candidates too (`ping_check`).
- `mount`: the mount point, the export path and the mount `options`. It also holds the full mount `command` and the read-ahead settings. `vip: auto` spreads clients round-robin over the tenant VIP pool.
- `elbencho`: benchmark size (threads, files, file size, block size), the commands, and `idle_before_read_seconds`: how long **Elbencho Auto Write/Read** idles between the write and the read (0 = no idle). A preset's `commands` can include an idle step anywhere as `- wait: <seconds>` (optional `label:`); the app waits, nothing runs on the client.
- `packages`: the packages to check and install on the clients.
  - `required`: each has a check command, per-distro package names and an optional custom install (elbencho comes from its GitHub static build).
  - `system`: plain OS package names (`curl`, `numactl`, `sysstat`, …), checked in the client's package database (`dpkg-query` / `rpm -q`) and installed with its package manager. Write `{ name: nfs, package: { apt: nfs-common, dnf: nfs-utils } }` when the name differs per distribution. In ⚙ Configuration it is a one-per-line list.
  - `package_managers`: the apt, dnf, yum and zypper `install`, `update`, `reinstall`, `remove` and `upgradable` commands.
- `vperfcheck`: download URL, install folder and data folder on the VAST view, default profiles, dataset, time, threads, `elbencho_args` (`--nolive`), whether to stop the services after a run, the run timeout, and the S3 bucket / prefix / endpoint. See [vperfcheck](#vperfcheck).
  - Each package can be installed, reinstalled or uninstalled on its own from the Required packages window that Presets → Client Cmds → Check packages opens (per client, or on all ticked clients). A `required` entry with a custom `install` also needs an `uninstall` command for that (elbencho and vastnfs have one); `reinstall` defaults to `install`.
- **VAST NFS driver (`vastnfs`)**: listed in `packages`. **Install missing** builds it from VAST's source package for the client's running kernel (build tools → `download.sh` → `./build.sh bin` → install the .deb/.rpm → `depmod` + initramfs), as in [Quickstart: Multipathing NFS Client](https://docs.vastdata.com/docs/quickstart-multipathing-nfs-client-download-and-install). It does **not** reboot: reboot the client to load the driver, then use **Check VAST driver version** (reads `/sys/module/sunrpc/parameters/nfs_bundle_version`). The client needs internet access to `vastnfs.vastdata.com`.
- **Mount**: `mount.command` can use `${tenant_vip.start}` / `${tenant_vip.end}` (the VIP pool this site's onboarding created) and `${mount.export_path}` (the created view's path). For example, NFSv3 over RDMA with multipath: `options: proto=rdma,port=20049,vers=3,nconnect=8,remoteports=${tenant_vip.start}-${tenant_vip.end}` and `command: mount -o ${mount.options} ${tenant_vip.start}:${mount.export_path} ${mount.mount_point}`. This needs the VAST NFS driver and an RDMA-capable NIC.
- `provisioning.on_existing`: `fail` (default) or `reuse` when a same-name object already exists.

## Trying it without the VPN

```bash
.venv/bin/uvicorn dev.mock_vms:app --port 9443 &
.venv/bin/python dev/mock_ssh.py 2222 &
.venv/bin/python dev/mock_ssh.py 2223 &
./run.sh dev/mock-site                 # a ready-made test site, http://127.0.0.1:8090
```

On Windows (after `run.bat` has created `.venv` once), each in its own window:

```bat
.venv\Scripts\python -m uvicorn dev.mock_vms:app --port 9443
.venv\Scripts\python dev\mock_ssh.py 2222
.venv\Scripts\python dev\mock_ssh.py 2223
run.bat dev\mock-site
```

The mock SSH servers only echo commands (and fake elbencho output). Nothing runs on your laptop.

## Export and import a site's configuration

**⤓ Export configuration** (open site card, or **⤓ Config** on any row of the site list) downloads a .zip with everything
that configures the site - **without logs or test results**:

- `config.yaml` - VMS connection, onboarding settings, VIP pool, SSH clients, presets, custom Linux commands,
  report targets and diagram labels, and the `vperfcheck:` defaults
- `site.yaml` - name, description, status (local paths are removed)
- `tenants.yaml` - onboarded tenants
- `results/.vperfcheck-state.json` - the vperfcheck settings saved in **Presets → Vast → Configure vperfcheck**
  (coordinator, clients, target, dataset, profiles, options). Only those: not what vperfcheck found on this lab's
  clients (installed version, last dataset, SSH trust), and never an S3 key. The imported site opens with them.
- optional (tick the box): saved passwords - `.client-secrets.json`, `.tenant-secrets.json` - and the onboarding state

**⤒ Import site** (Test site tab) creates a **new** test site from such a zip; it never overwrites a site (a name that is
taken gets `-2`, `-3` …). It also accepts a full **Download with results** zip and skips the logs in it. The zip is
checked first: one site per zip, no paths outside the site folder, a valid config.yaml. Imported password files are
readable by the owner only (mode 600).

## vperfcheck

[vperfcheck](https://docs.vastdata.com/docs/vperfcheck-automated-performance-testing) is VAST's profile-driven
elbencho wrapper. One client, the **coordinator**, runs `vperfcheck.sh`; it starts `elbencho --service` on every
benchmark client over passwordless SSH and runs them together (elbencho's distributed mode), so each profile gives one
aggregate result. It is in tab ② under **Presets → Vast** (shown once a console is connected):

- **⚙ Configure vperfcheck** opens a window with all the settings (coordinator, benchmark clients, target, dataset,
  profiles, run and advanced options), the command line, and **✓ Pre-check**, **Dry run**, **▶ Run** and **■ Stop**.
  **💾 Save** keeps the settings in this browser and on the site (`results/.vperfcheck-state.json`, so another
  browser starts from them) and logs them in 🗒 Logs (`<time>-vperfcheck-config`, with the command line). Closing
  with unsaved changes asks first; Run and Dry run save what is on screen first. A typed S3 key pair is never saved.
- The other Vast items (**⤓ Install / update**, **🔑 Set up SSH trust**, **🔌 Test SSH between clients**, **Remove SSH
  trust**, **📦 Download Package results**) use the saved coordinator and benchmark clients and show their steps in a
  small window. **■ Stop vperfcheck** appears there while a run is going.

1. **⤓ Install / update**: downloads the latest stable vperfcheck onto the shared VAST view
   (`vperfcheck.install_dir`, default `<mount point>/vperfcheck`) from the coordinator. Earlier results are kept.
   It needs `jq` on the coordinator (in `packages.system`), elbencho on every client (Required packages) and, for S3,
   the AWS CLI on the coordinator (`aws-cli` in Required packages).
2. **🔑 Set up SSH trust**: lets the coordinator log in to the ticked clients without a password. It only adds:
   a dedicated key `~/.ssh/vast_test_bench_vperfcheck` on the coordinator (existing keys are not touched), that key
   once in each client's `~/.ssh/authorized_keys`, and a marked block at the top of the coordinator's `~/.ssh/config`
   for those hosts (that key, `StrictHostKeyChecking accept-new`; `User` / `Port` only where they differ). A backup of
   the config is kept, nothing is removed and the SSH server is not changed, so the logins you use today keep working.
   Running it again adds hosts. **🔌 Test SSH between clients** logs in from the coordinator to each client the way
   vperfcheck does. **Remove SSH trust** takes out the ticked hosts (the key goes when none is left).
   vperfcheck reaches the clients by their `host` in the config.
3. **✓ Pre-check**: vperfcheck and its version, jq / AWS CLI, the mount and elbencho on every client, SSH from the
   coordinator, the data directory, and for S3 the keys, the endpoint and the bucket.
4. **▶ Run vperfcheck** (or **Dry run**): file system (`--dir` = `vperfcheck.dir`) or S3 (`s3://<bucket>/<prefix>/`),
   dataset **Auto size** (`--prep auto`) / **Use the existing dataset** / **Fixed size**, the profiles, time, threads,
   `--noloop`, `--lat`, and advanced options (block, file size, I/O depth, direct, random, auto size / time, elbencho
   pass-through). The command line is shown before it runs. The coordinator's console shows progress; **■ Stop** stops
   vperfcheck and the elbencho services. After a failed run the app stops the services too.
5. **Results** come back from the coordinator (vperfcheck's `results.json`, `results.csv`, `results.txt`): one row per
   profile in **Performance results** and 🗒 Logs (json, txt and csv), the Chart, and the PowerPoint / Word reports.
   **📦 Download Package results** runs vperfcheck's packaging helper and downloads the `.tar.gz`.

**S3**: the keys are the S3 user onboarding created (or a key pair typed in Configure vperfcheck, used for that run only and never
saved); the endpoint is the tenant VIP pool in vperfcheck's bracket form (`http://10.0.0.[1-8]`) unless
`vperfcheck.s3.endpoint` is set; the bucket is the view's bucket (vperfcheck creates it if it's missing). The keys go to
the coordinator in a file readable only by the SSH user, read by the run and deleted at once; the secret is masked in
the console and never written to the logs.

**Good to know** (vperfcheck 2026-10-02):
- vperfcheck keeps one dataset description per install, so **Use the existing dataset** only works for the target it
  laid out last; the app refuses it for another target and tells you to use Auto size or Fixed size.
- On an **empty S3 prefix**, vperfcheck wrongly accepts the dataset for Fixed size / Use existing and then fails
  reading; the app checks the prefix and asks for **Auto size** for the first run on it.
- Use the **same login user on every benchmark client** (or root): each client's elbencho service runs as its own
  user, so write profiles can't overwrite files another user laid out. The pre-check warns when they differ.

## Performance report (PowerPoint and Word)

**📊 Performance report** (Performance results card, tab ②, or 🗒 Logs) builds a **PowerPoint** deck, a **Word** document
or both from the performance runs you pick (elbencho read and write results only). The Word report follows the usual
benchmark write-up: document history, overview (objective / method / test tool), lab environment table, network
architecture, data path and sample mount command, result summary, estimated vs actual, results per run (First / Last
Done MiB/s, GB/s, elapsed), per-client throughput, latency, cluster performance, execution commands, observations and a
disclaimer; charts are images. It uses the **report template** uploaded in ⚙ Configuration → Report template (.docx or
.dotx; kept per site and included in Export configuration): its page setup, header / footer and logo, fonts and the styles
Title, Heading 1, Heading 2, List Bullet and Table Grid. Placeholders `{{title}} {{site}} {{tenant}} {{date}} {{author}}
{{vms}} {{period}}` are filled in anywhere; a paragraph holding only `{{content}}` marks where the report goes (a cover page
before it is kept). **⤓ Starter template** gives a template to edit in Word. Without an upload a plain built-in layout is used.

The PowerPoint deck contains:

- **Cluster Network Architecture** (page 2) - drawn from the VMS inventory (`/cboxes/`, `/cnodes/`, `/dboxes/`,
  `/dnodes/`, `/eboxes/`, `/nics/`): split design = clients → 2 front-end switches → CNodes → 2 back-end switches → DBoxes,
  converged design = clients → 2 switches → EBoxes; red / blue = fabric A / B, dashed = back-end, with box counts, the
  DBox model and per-node port speeds. The VMS doesn't list switches, so their names (and an optional DBox model name)
  are typed in the report window and saved per site under `report.labels`.
- **Test Data Path** - clients → NFS mount (protocol, nconnect, multipath) → VIP pool → VAST cluster (name, VAST OS,
  CNodes / DBoxes), tenant, view, QoS and quota.
- **Performance Highlights** - peak read / write throughput, peak IOPS, P99 latency.
- **Estimated vs Actual** - the expected figures you type in the report window (read / write GB/s, IOPS, P99 latency and
  where they come from; saved per site under `report.expected` in config.yaml) against the measured results, with % of target.
- **Throughput per Run** - chart plus time taken, IOPS, average / P99 / P99.9 latency per run.
- **Cluster Performance** - what the VAST cluster served during each run, from the VMS analytics API
  (`GET /monitors/ad_hoc_query/`, cluster `ProtoCommon` bandwidth / IOPS / latency, 10-second samples): whole-run average
  (comparable with the clients' figure), sustained and peak, with notes on slow phases or large differences.
- **Latency** - P50 / P99 / P99.9 per run and min / avg / percentiles / max per client. Needs elbencho with
  `--lat --latpercent` (add `--latpercent9s 1` for P99.9) in the preset.
- IOPS per run, throughput per client, all results, and the test setup (tenant, clients, mount options with the real VIPs,
  benchmark commands). Numbers are the same as the Performance results table: summed across clients, decimal units
(MB/s, GB/s or TB/s). Charts are native PowerPoint charts, so they can be edited. It uses the tester manual's template
(`app/assets/report-template.pptx`). The report is saved in the site's `results/` folder as
`<time>-performance_report.pptx` with a `.md` summary and is listed in 🗒 Logs, where **⤓ Download PowerPoint** saves it.

## Version

The version is in [`VERSION`](VERSION) and shows beside the title in the top bar (for example `v1.3.0`).
If you pull a newer version while the app is running, the badge turns amber and reads `v1.3.0 → v1.4.0 (restart)`
until you restart the app - click the amber badge, or **🔄 Restart app** in the top bar. The restart window lists
what it interrupts (connected SSH consoles are disconnected; a running test, onboarding or site delete blocks it),
then restarts the app, counts down 5 seconds and reloads the page with the same test site open. What changed in each version is in [`CHANGELOG.md`](CHANGELOG.md).

## Notes

- The REST payloads live in the config, so if a VMS release renames a field you only edit `config.yaml`.
- SSH connections:
  - Handshakes are made one at a time, and a timed-out handshake is retried (`ssh.connect_retries`).
  - A 30 s keepalive notices dead links.
  - If a connection drops, the console says why and shows **↻ Reconnect**.
  - After connecting, the commands run in parallel.
- The app binds to 127.0.0.1 because it holds credentials and shell access.
