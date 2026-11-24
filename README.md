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
3. **Performance**: elbencho Write / Read / Auto Read-Write presets, configurable in the `elbencho:` config section. Results are parsed and summed across clients, shown in MB/s, GB/s or TB/s, and exported to JSON, TXT and CSV.

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
- The 📁 Test site tab lists all sites in a table, one row per site, each with **Open site** and **Delete site**. **＋ New Test Site** is at the top right.
- **Delete site** deletes the site's local records only: configuration, `tenants.yaml`, saved credentials and results. It moves the folder to `sites/.trash/`, so you can restore it by moving the folder back. **Nothing on the VMS is touched.** The confirmation, and the message after deleting, list every tenant the site still has on the VMS (name, tenant id, VMS, VIP range) and the order to delete its objects in VMS manually. To remove them from the VMS instead, open the site and use 🧹 Cleanup before deleting it. Only folders directly inside `sites/` can be deleted from the app.

Secrets can stay out of the file: `api_token: "${env:VMS_API_TOKEN}"`, `password: "${env:SSH_PASSWORD}"`.

```bash
export VMS_API_TOKEN=... SSH_PASSWORD=...
```

## Tabs

| Tab | What it does |
| --- | --- |
| 📁 Test site | Current site details (folder, created, VMS, contents), edit name/description, download zip, replicate instructions, create / open / switch sites. Only tab available until a site is open |
| ① Onboarding & API tests | VMS connection (editable), onboarding settings grouped in boxes - Tenant, Users, VIP Pool (incl. number of IPs / CIDR / VLAN), Quota, QoS, View Policy, View - each showing its step status; **💾 Save as site defaults** writes them to the site's config.yaml (`${env:…}` references are kept), VIP-pool IP map showing every /24 the candidate ranges touch (.0–.255, labelled rows; IPs outside the candidate ranges are outlined and can't be picked; click a free IP to choose where the block starts), **Create Tenant** (runs all steps) / single step, Credentials (generated admin password + S3 keys), Export report, Cleanup (reverse order; objects that were reused are left in place) |
| 🏢 Tenants | Summary card per onboarded tenant (IDs, admin + login URL, VIP range, view/NFS export + mount example, S3 endpoint/bucket/access key, QoS, quota), loaded from `tenants.yaml`. The **admin password** and **S3 secret key** are masked: 👁 shows, ⧉ copies. They come from `.tenant-secrets.json`, so they survive *Reset state* and later runs. Opens automatically on start-up when tenants exist. **Verify on VMS** checks every object still exists; Cleanup marks a tenant *deleted* (kept as history) |
| ② Clients · Mount · Performance | Saved clients list (custom hosts are saved to `clients:`, passwords to a hidden `.client-secrets.json`), consoles with **⏏ Disconnect / ↻ Reconnect**, **⤓ .md** export and **⤢** large view, **Check packages / Install missing / Update / Check VAST driver version**, presets (Pre-check, Mount NFSv3, Set read-ahead, Run elbencho, Unmount), result summary and exported files |
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
- `packages`: the required packages. Each has a check command, per-distro package names and an optional custom install (elbencho comes from its GitHub static build). The apt, dnf, yum and zypper commands are also set here.
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

## Notes

- The REST payloads live in the config, so if a VMS release renames a field you only edit `config.yaml`.
- SSH connections:
  - Handshakes are made one at a time, and a timed-out handshake is retried (`ssh.connect_retries`).
  - A 30 s keepalive notices dead links.
  - If a connection drops, the console says why and shows **↻ Reconnect**.
  - After connecting, the commands run in parallel.
- The app binds to 127.0.0.1 because it holds credentials and shell access.
