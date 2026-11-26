# Decisions

This file records the changes and design decisions made to VAST Test Bench, with the reasons for them. It is grouped by area, and the latest decision in each area wins. Paths are relative to the repo root.

Last updated: 2026-11-24.

---

## Running the app

### One launcher for Windows, macOS and Linux
- **Decision.** `start.py` does all the setup and uses only the Python standard library. It is wrapped by thin scripts:
  - start: `run.bat` (Windows), `run.sh` (macOS/Linux), `run.command` (macOS double-click);
  - stop: `stop.bat`, `stop.sh`, `stop.command` (all call `start.py --stop`).
- **What the launcher does.**
  - Requires Python 3.10 or newer.
  - Creates `.venv`.
  - Installs `requirements.txt` only when that file changes (it keeps a hash stamp).
  - Picks the port in this order: `--port`, then `$PORT`, then the site's `app.port`, then 8080.
  - If the app already runs on that port, it opens the browser on it instead of starting a second copy.
  - Binds to `127.0.0.1` only.
- **Why.** Testers on any OS should be able to start the app in one step. The old `run.sh` was macOS/Linux only and hid install errors.
- **Ubuntu without `python3-venv`.**
  - What happens: the launcher creates the venv without pip, then installs pip with the official `get-pip.py`. No sudo is needed. A broken or half-created `.venv` is detected and rebuilt.
  - Why: stock Ubuntu 24.04 cannot create a venv without that package. This was seen on a real test VM.
- **Stop.**
  - How it finds the app: each running instance writes `.run/app-<port>.json` with its pid.
  - Safety check: stop ends a process only if its port answers as VAST Test Bench, which guards against a reused pid.
  - Clean exit: a `.stopping` marker lets the run window exit cleanly (exit code 0) instead of reporting an error.
- **Windows compatibility.** The optional ping check (`app/ipam.py`) uses Windows `ping` flags and runs `subprocess` in a thread, so it works with every event loop.
- **Testing.** macOS (fresh clone) and Ubuntu 24.04 were tested. Windows has **not** been tested yet.

### Restarting the server while someone uses the app
- **Decision.** Before restarting a running app server, check `GET /api/runs` (active runs) and `GET /api/sessions` (open consoles). If anything is active, don't restart.
- **Why.** A restart ends every SSH session and the commands running in them, and a run's log is only written when the run finishes. One restart cut off a VAST NFS driver install on a client mid-build, leaving no log and no package.
- **How to apply.** Changes to `static/*` only need a browser reload. For backend changes, test on a second instance (another port, a scratch copy of the site).

---

## Configuration and test sites

### `config.yaml` writes must never corrupt the file
- **Decision.** `app/config_store.py`:
  - **Re-read check:** re-reads its own output before writing. If the output is not valid YAML, or doesn't load back as exactly the intended data, nothing is written.
  - **Comment fix:** comments that sat above a list or map that became empty (or a scalar) are moved to an end-of-line comment.
- **Why.** Removing the last saved client produced invalid YAML: `[]` on its own line, and the next key merged into it. The app kept running from memory, but the next restart would have failed.

### The ⚙ Configuration editor must not overwrite newer changes
- **Decision.** The editor sends the file's mtime from when it loaded it (`base_mtime`). The server answers `409 … Nothing was saved. Click Revert …` if the file changed on disk since then.
- **Why.** The editor saves its whole copy of the file. Twice, a save from an open editor silently removed changes made in the meantime: new presets added by another edit.

### Environment-variable warnings
- **Decision.**
  - Unset `${env:…}` references are shown as amber notes, with a hint where the meaning isn't obvious (e.g. `TENANT_ADMIN_PASSWORD` empty means a password is generated).
  - The banner can be closed. Closing is remembered per site until a new warning appears.
- **Why.** They read like errors after saving a VMS token, although the save had worked.

### Session values vs. site defaults
- **Note.** Values typed in Tab ① last for the session only. **💾 Save as site defaults** writes them to `config.yaml`.
- **Why it matters.** A restart (or a config reload) falls back to the file. This is why a mount once used a view path built from the default tenant name.

### "⇠ All sites" button
- **Decision.** A header button closes the open site and returns to the site list. It asks first if consoles are open.
- **Constraint.** It refuses while a tenant onboarding run is in progress.

### Public repo hygiene
- **Decision.** The public repo contains no internal lab details: example configs, the template and the mock site use placeholders (`vms.example.com`, `10.20.0.x`).
  - Templates for your own clusters go in `templates/local-*.yaml`. The app lists them, but they are git-ignored.
  - Also git-ignored: test sites, results, `config.yaml`, `.client-secrets.json`, `.tenant-secrets.json`, `*.secrets.json`, `.env`, `.run/` and `.venv/`.
  - Exception: `dev/mock-site/config.yaml` is included, because it holds only mock values and the offline mock setup needs it.
- **Why.** The GitHub repo is public.

---

## Tenant onboarding (Tab ①)

### Onboarding settings layout
- **Decision.**
  - **Tenant name row:** the tenant name has its own row at the top. Every other name is built from it.
  - **Derived names:** the admin, role, S3 user, VIP pool, quota, QoS policy, view policy, view, path and bucket names show only the resolved name. ✎ opens the template (`${vars.tenant_name}-…`) for editing (Enter applies, Esc cancels). A name that no longer follows the tenant name shows a **custom** tag.
  - **Rows:** each card is a list of label/value rows with short labels. Brief notes sit under the label ("0 = unlimited · min 100 MB/s"). Unit conversions appear only as tooltips, unless a value is invalid (e.g. below the VMS minimum of 100 MB/s).
  - **Status:** shown in plain words: not created yet, *n* of *m* created, ✓ created, ✕ failed, … running.
  - **Grid:** 3 cards per row (2 at ≤ 1100 px, 1 at ≤ 700 px), all the same height.
- **Why.** The previous layout showed every name twice (template and value), broke names mid-word, and was noisy overall.

### "Create Tenant" button
- **Decision.** "Run all" is renamed **▶ Create Tenant**. It still runs all 11 steps, and its tooltip lists them.
- **Live progress:** steps show as `queued` → `running` → result while the run goes. The page polls the server's per-step state and shows a progress bar with the current step and its elapsed time.
- **Why.** The old button marked all steps "RUNNING" until everything had finished, so the page looked frozen.

### VIP pool selection
- **Decision.**
  - **Map:** the IP map lives in a modal, opened from the VIP Pool card with **Choose VIP range…**.
  - **Setting:** `vip_pool.auto_select` (default on). On: step 1 picks the first free block. Off: step 1 uses only the range chosen on the map. It never falls back to another block, and it fails clearly if nothing is chosen or the chosen block is taken.
  - **Clicking:** clicking any usable free IP chooses the block of *n* that contains it, starting earlier near the end of a free run. Hovering previews that block.
  - **Small gaps:** free IPs in runs too small for a block are dimmed and explain why when clicked. An invalid click keeps the previous choice.
- **Why.** Before, the block had to start exactly at the clicked IP, so most clicks inside a free run silently failed.

### NFS root squash: off for test clients
- **Decision.** The onboarding view policy uses `nfs_no_squash: ["*"]` and `nfs_root_squash: []`. Root on the test clients acts as root on the view. This is the default in the site configs, `config.example.yaml` and the templates. For tenant `test-auto-tenant-02` it was changed on the VMS directly (`PATCH /viewpolicies/<id>/`).
- **Why.** Presets run elbencho and `mkdir` with `sudo`. With root squash and the **NFS** security flavor, the view's top folder is `755` and owned by `nobody`, so the squashed root got "Permission denied" creating `elbencho/`. (With **Mixed Last Wins** the top folder had been `777`, which is why it worked on the first tenant.) VMS needs at least one squash entry, and `nfs_no_squash` satisfies that.
- **Trade-off.** Any client that can reach the VIPs has root access to the view. That is fine for a benchmark lab; for anything else, list specific client IPs in `nfs_no_squash`, or keep root squash and give the benchmark folder to the SSH user.

### Mount uses what onboarding created
- **Decision.**
  - **View path:** when the view exists, mount commands use the created view's path.
  - **VIP range:** `${tenant_vip.start}` / `${tenant_vip.end}` come from the VIP pool that onboarding created, not from a re-run VIP proposal.
- **Why.** Session-only tenant names fall back to the config default after a restart, which produced a mount of the right VIP with the wrong path.

### QoS and quota limit switches (🏢 Tenants)
- **Decision.** Each active tenant card has **QoS limits** and **Quota limits** switches. Their state is read live from the VMS.
  - **Quota:**
    - Off: save the limits, then set `hard_limit` / `soft_limit` / inode limits to `null` (how the VMS stores "no limit").
    - On: restore the saved values.
  - **QoS:**
    - Off: detach the policy from the tenant's view (`qos_policy_id: null`). The policy itself is kept unchanged.
    - On: re-attach it.
  - **Saved values:** kept in the tenant's `tenants.yaml` record under `limits:`. Onboarding updates (`upsert`) preserve that record.
  - **Verification:** every change is checked with a GET afterwards.
- **Why the QoS approach.** The API docs say "0 means unlimited", but the VMS refuses a policy whose limits are all 0 ("At least one Qos Static or Static Total Limit should be set").

---

## Clients, consoles and presets (Tab ②)

### Saved SSH clients
- **Decision.**
  - **Custom hosts:** saved to `clients:` in the site's `config.yaml` (the **Save to client list** box is ticked by default). Port and username are only written when they differ from the `ssh:` defaults.
  - **Passwords:** go to `.client-secrets.json` (mode 600), never into `config.yaml` or the site zip.
  - **Management:** a saved-clients table offers Connect and Remove.
- **Why.** Testers re-connect to the same VMs over and over.

### Consoles survive page reloads
- **Decision.**
  - **Server side:** SSH sessions live on the server, independent of the page. The shell is read continuously and the last ~2 MB of output is kept and replayed when a page re-attaches.
  - **Several pages:** any number of pages can show a console. Closing or reloading a page only detaches it.
  - **What is restored:** the ✓ ticked state, "input needed" prompts and in-progress runs.
  - **⏏ Disconnect** closes the SSH connection but keeps the console and its output. **↻ Reconnect** reopens it in the same console. **✕** removes it everywhere.
  - **Idle close:** consoles that no page has shown for `ssh.idle_close_minutes` (default **60**, `0` = never) are closed, unless a preset is running.
  - **Server restart:** ends all consoles. This was accepted; making them survive would need tmux on the clients.

### Console view
- **Decision.**
  - **Commands:** preset commands are echoed like typed shell commands (`[Preset 1/2] user@host$ cmd  (sudo)`).
  - **⤓ .md:** exports the console to Markdown in the site's results folder. Known SSH passwords are masked.
  - **⤢:** opens the same live console in a large modal. Esc goes to the terminal (vim/less need it); close the modal with ⤡ or by clicking outside.
- **Rendering.** The terminal uses xterm's **WebGL** renderer, and output is written in batches (one write per ~16 ms, timer-based so background tabs keep flushing). `?renderer=dom` forces the old DOM renderer.
- **Why WebGL.** Two "Aw, Snap" tab crashes were Chrome crash dumps showing a **browser extension** running out of memory in `JSON.stringify`, not the app itself (its heap was 20–49 MB). The DOM renderer changed the page thousands of times during heavy output. WebGL cut that from 3,911 to 65 changes for 2.7 MB of output.
- **For users.** Exclude 127.0.0.1 from page-reading extensions.

### Required packages and presets cards
- **Decision.** Both cards are hidden until a console is connected; a hint is shown instead. Both can be folded to their title bar, and the folded state is remembered in the browser.
- **Generic preset options:**
  - `section: packages` puts a preset button in the Required packages card.
  - `confirm: "…"` asks before running a preset.

### Package checks
- **Decision.** Check commands must not mask a missing command. Use `command -v X >/dev/null && X --version | head -1`.
- **Why.** `X --version | head -1` returns `head`'s exit code, so a missing `elbencho` was reported as installed and **Install missing** skipped it.

### VAST NFS driver
- **Decision.**
  - **Install missing:** `vastnfs` is a required package, built the way the VAST docs describe: build tools for the running kernel → `download.sh` → `./build.sh bin` → install the `.deb`/`.rpm` → `depmod` plus initramfs/dracut.
  - **No automatic reboot.** The driver loads after you reboot the client.
  - **Concurrency:** the install takes a lock (`flock`) on the client and builds in its own temp folder (`mktemp`).
  - **Check VAST driver version:** reads `/sys/module/sunrpc/parameters/nfs_bundle_version` and shows installed vs. loaded.
- **Why the lock.** Two installs started at the same time on one client overwrote each other's build folder.

### Mount
- **Decision.** The default mount for **all new test sites** (`config.example.yaml` and the templates) is NFSv3 over TCP with VAST multipath across the tenant's whole VIP pool:
  - `options: proto=tcp,vers=3,nconnect=8,remoteports=${tenant_vip.start}-${tenant_vip.end},spread_reads,spread_writes`
  - `command: mount -o ${mount.options} ${tenant_vip.start}:${mount.export_path} ${mount.mount_point}`
  - `${tenant_vip.*}` comes from the VIP pool that onboarding created, and `${mount.export_path}` from its view. Presets add `sudo` automatically for non-root users. The tenant card's "Mount example" is built from the same settings.
  - **Requirement:** the VAST NFS driver must be installed and loaded on the client (**Install missing**, then reboot). The stock Linux NFS client rejects `remoteports` / `spread_*` with "incorrect mount option".
  - Use `proto=rdma,port=20049` only on clients with an RDMA NIC. `20049` is the RDMA port; NFS over TCP is 2049.
  - Sites created before this change keep whatever their own `config.yaml` says (both current sites already use the default above).
- **Why.** The test VMs have no RDMA device (mount gives "Protocol error"). `proto=tcp,port=20049` hangs, because nothing listens for TCP on that port.
- **Stale mounts.** **Mount NFS** first unmounts whatever is still mounted at the mount point (forced, then lazy). **Unmount** tries normal, then forced, then lazy.
  - **Why:** a tenant and its view were deleted on the VMS while the clients still had the view mounted. After that, anything touching the mount point failed with "Stale file handle", including the next site's `mkdir`. That blocked mounting the new tenant.
  - **Practice:** run **Unmount** on the clients before deleting a tenant or view, or before **🧹 Cleanup**.

### Benchmark presets (elbencho)
- **Decision.** A config section `elbencho:` works like `mount:`. Commands use it as `${elbencho.…}`, and it holds:
  - parameters: `dir`, `threads`, `dirs`, `files`, `file_size`, `block_size`, `flags`;
  - commands built from them: `check_mount`, `mkdir_command`, `write_command`, `read_command`, `cleanup_command`, `delete_command`, `drop_caches_command`.
- **Buttons:**
  - **Elbencho Write** (files are left for Read);
  - **Elbencho Read**;
  - **Elbencho Auto Write/Read** (write → read → delete; keeps the old `elbencho` id so older results stay linked);
  - **Delete Elbencho Files** (asks first);
  - **Clear Linux Cache** (`sync; echo 3 > /proc/sys/vm/drop_caches`, with `free -h` before and after);
  - **Ping VMS with VIP** (`ping -c 5 ${tenant_vip.start}`).
  - **Client info** (read-only, no sudo): OS and version, kernel, VAST NFS driver (loaded, or installed and waiting for a reboot), CPU count and model, RAM, and per network interface its IPv4 address(es), link speed, state and MTU. Virtual NICs (e.g. virtio on QEMU) report no link speed; that shows as "speed n/a (virtual NIC)".
  - **Mount Status** (read-only, no sudo): what is mounted at the mount point (source, type, options), whether it is this site's tenant view, whether it responds (`stat` with a 5 s timeout, so a stale mount is reported instead of hanging), and usage. The run fails when nothing is mounted or the mount doesn't respond.
- **Safety.**
  - **Quoted paths:** every benchmark path is quoted.
  - **Mount check:** every benchmark preset first checks that the mount point is mounted.
  - **Delete scope:** Delete refuses anything outside `<mount point>/elbencho/`.
  - **Why:** an unquoted client name with spaces (`test auto vm 01`) split the path, and elbencho wrote about 20 GB to each VM's **local** root disk, which filled it to 100%.
- **Perf Test removed.** It was a combined throughput + random IOPS + metadata suite. It was removed at the user's request, together with its settings (`rand_*`, `md_*`).
- **Known behaviour.** `--nolive` prints nothing until a phase ends, so long phases look idle. Defaults write 1 TiB per client (32 × 1 × 8 × 4 GiB).

### Presets dropdown menus
- **Decision.** The Presets card shows one dropdown per menu instead of a row of buttons:
  - **Client Cmds**: Pre-checks, Client info;
  - **Connection**: Ping VMS with VIP;
  - **File System**: Mount NFS, Mount Status, Set Read-ahead, Unmount;
  - **Performance Test** (highlighted): Elbencho Write, Elbencho Read, Elbencho Auto Write/Read;
  - **Utilities**: Delete Elbencho Files, Clear Linux Cache (Delete is shown in red and still asks first);
  - **Customs**: every preset with `menu: Customs`, or without a `menu:` key that isn't built in. When there are none it links to the editor below.
- **How.** Each `command_groups` entry takes an optional `menu: <name>`. A name that isn't one of the five adds its own dropdown before Customs. Items keep their config order. Sites created earlier without `menu:` keys still group correctly: the page falls back to the built-in id → menu map.
- **Renamed labels** (ids unchanged, so results and logs stay linked): Pre-check → Pre-checks, Client Info → Client info, Ping VIP → Ping VMS with VIP, Mount NFSv3 → Mount NFS, Set read-ahead → Set Read-ahead, Elbencho Auto Read/Write → Elbencho Auto Write/Read.
- **Keyboard.** Enter/Space opens a menu, ↑/↓ moves, Esc closes; a click outside closes it.

### Custom Linux commands (⚙ Configuration)
- **Decision.** The Configuration tab has a **Custom Linux commands** card (the last card in the tab; it folds to its title bar like Required packages and Presets, and the choice is remembered) to create, edit, reorder and delete the user's own presets. Each one appears under **Presets → Customs** right after **💾 Save commands**.
- **Per command:**
  - **Name**: the label in the Customs menu (required, unique);
  - **One command per line** (each line is its own command, run in order, stops at the first failure) or **One script** (the whole text runs as one bash script, for `if`/`for`/variables);
  - **Run with sudo** (off by default), **Ask before running** (with an editable message), **Save elbencho results** (parse the output as a performance run).
  - Template values work as in the built-in presets (`${mount.mount_point}`, `${tenant_vip.start}`, `${client.name}`, …).
- **Storage.** They are ordinary `command_groups` entries with `menu: Customs` and an id `custom_<name>`, in the site's `config.yaml`. Custom entries written by hand that the card can't show (other keys, per-command display names) are listed read-only and kept exactly as they are.
- **Saving touches only the custom entries.**
  - It rewrites only the `command_groups` block; every other line of `config.yaml` stays byte-for-byte the same.
  - Comments on the built-in presets are kept; deleting all custom commands gives back the original file.
  - It backs up the previous file (`config-backups/`) like every config save.
- **Why a separate card (not the generic form):** the generic form replaces the whole `command_groups` list when an item is removed, which drops every comment in it, and it shows commands as raw JSON/YAML.
- **Conflicts.** Saves carry the file's modification time; if `config.yaml` changed in the meantime (another tab, the full editor, a hand edit), nothing is saved and the card says so. After a custom save the full editor reloads, unless it has unsaved edits, in which case it warns to Revert first.
- **Export / Import.**
  - **⤓ Export** downloads the card's current list (unsaved edits included) as `custom-commands-<site>-<date>.yaml`: a small, readable file (`format: vast-test-bench/custom-commands`, `version: 1`, then per command `name`, `mode`, `commands`, `sudo` and optionally `ask_before_running` / `save_elbencho_results`). Hand-written entries the card can't show are not exported.
  - **⤒ Import** reads such a file (from this or another site), or a `command_groups:` list copied from a config.yaml (built-in presets in it are skipped). Nothing is saved on import: the commands are added to the card as unsaved changes, to be reviewed before **💾 Save commands**, because they run on the clients.
  - **Same name:** the page asks **Replace existing** (keeps the existing id, so its logs and results stay linked) or **Keep both** (adds "name (2)").
  - Bad files (not YAML/JSON, unknown `format`, no commands, over 1 MB) are rejected with a message.
- **Site switch.** Opening another test site reloads the card for that site (it used to keep showing the previous site's commands, and a save then failed as "changed after you opened it").
- **Menu look.** Red stays reserved for built-in destructive presets (Delete Elbencho Files). A custom command that asks first shows "…" after its name.

### Performance results
- **Decision.**
  - **Card:** the Performance results card shows only performance (elbencho) runs, with a run picker.
  - **Units:** throughput in **MB/s, GB/s or TB/s**, decimal (1 GB/s = 1000 MB/s, like the QoS settings), converted from elbencho's MiB/s.
  - **⤓ CSV:** one aggregate row per operation plus one row per client, in the chosen unit (named in the columns), including the exact command.

---

## Performance chart (📈 Chart)

- **Data.** Each performance (elbencho) run is one **test session**. Each benchmark phase (Write, Read) has a start time, a duration (elbencho's elapsed time) and an **average** throughput and IOPS (elbencho's "last done" values), in total and per client. No per-second samples are recorded, so the chart can't show the variation within a phase.
- **Timeline = histogram.** Even time buckets are sized automatically to about one column per 10 px (10 s, 15 s, 30 s, 1 min, … up to 1 day). Each column is the **time-weighted average** of the phases that ran in it: a bucket half-filled by a 5 GB/s write shows 2.5 GB/s. Write and Read sit side by side where both ran (clients finish at different times, so phases of different clients can overlap).
  - **Why a histogram:** the user asked for one instead of the first version, which drew each phase as a flat line over the time it ran.
- **Compare sessions.** Grouped bars, one group per session, using each operation's best phase.
- **Controls.**
  - **Above the chart:** view, metric (throughput or IOPS), unit (MB/s, GB/s, TB/s, decimal) and a table view.
  - **Right:** the session list, with Show all, Hide all and "only". The timeline zooms to the visible sessions.
  - **Legend:** toggles Write and Read.
  - **Remembered:** hidden sessions are kept per site in the browser, and view, metric and unit globally.
- **Color.** Write = categorical slot 1 (blue), Read = slot 2 (orange), in fixed order. Validated with the dataviz palette checker against the app's light (`#ffffff`) and dark (`#161b24`) card surfaces; all checks pass in both modes.
- **Built in the page** from the saved run JSON (`static/chart.js`), so adding the tab needed no server change.

---

## Logs (🗒 Logs)

- **Decision.**
  - **What is listed:** every run log, onboarding report and console export, one row per run (`.txt` / `.json` / `.md` together).
  - **Layout:** a reading pane, with the list on the left third (grouped by day, filter by type/status, search, ↑/↓) and the selected file on the right two-thirds (txt / pretty json, copy, download, open raw, light highlighting). Both panes scroll inside a split that ends at the window bottom.
  - **Archive logs:** zips the logs into `results/archive/logs-<time>.zip` and removes them from the list.
  - **Purge logs:** deletes them permanently. Archives are kept.
  - **Scope:** both leave performance results alone unless that is ticked, and both refuse while a run is in progress.
- **Why.** Every preset and package run writes a log automatically. They had been shown as "Performance results", which they aren't.

---

## Open items

- Test `run.bat` / `stop.bat` on a real Windows machine.
- Optional: show the running step and its elapsed time in the preset run status line, so silent elbencho phases are visibly working.
- All changes up to 2026-11-24 are pushed to GitHub.

## Delete site removes the tenant from the VMS too (modal, 4 steps)

- The user types the site name and the tenant name (as on the VMS). With a tenant on record the typed name must match it; with none, any name may be typed, and a tenant that is not in the site's records needs an extra "I'm sure" tick on the review step. The VMS default tenant (id 1 / `default`) is refused.
- Scan (`POST /api/sites/teardown/scan`): the tenant is looked up by exact name, then every asset kind is listed. Most VMS list endpoints don't filter by tenant (or ignore the filter), so each object is kept only if its own field proves it is the tenant's: `tenant_id` (views, view policies, QoS, quotas, VIP pools), `tenant_id` + `user_type: TENANT_ADMIN` (managers), `tenants == [id]` (roles; shared roles are left and noted), the tenant's `local_provider` (S3 users - and only if no other tenant uses that provider; otherwise only the S3 user this site recorded).
- Run (`POST /api/sites/teardown/run`, NDJSON stream): deletes only what the user reviewed that still exists, in the order view → view policy → S3 user → QoS policy → quota → VIP pool → tenant admin → admin role → tenant → local provider. Before each DELETE the object is read again and its ownership re-checked; after it, the app polls until GET returns 404 (tenant and VIP pool deletes are asynchronous). The first failure stops the run, marks the tenant `partial` in tenants.yaml and keeps the site; "Scan again and continue" picks up what is left. A failed delete of the tenant's `default` view policy is skipped (the VMS removes it with the tenant).
- Only when the VMS side is clean is the folder moved to `sites/.trash/` (and the site closed if it was open).
- The modal can't be closed while deleting. Animations respect `prefers-reduced-motion`.
- `dev/mock_vms.py` now ignores `page`/`page_size` and handles `__icontains` filters.

## Force delete of a tenant, and the delete log

- The VMS can refuse `DELETE /tenants/{id}/` (seen: HTTP 503 "Failed to delete tenant's empty subdirectories. Please, consider using "force" to delete the tenant."). The 5.5 API has `force_remove=true` on that call: "Forces deletion of the tenant, even if files exist in the tenant directory".
- When the tenant delete fails, the modal shows **⚠ Force delete tenant…**. The user must type `force delete <tenant name>`; the server checks the phrase again (400 otherwise). The run is repeated with that tenant forced; before the forced DELETE the tenant is scanned again and the force is refused if anything else (view, policy, user, …) is still under it - force only covers the tenant itself.
- The app-wide log `sites/.delete-log.jsonl` (JSON lines; `sites/` is git-ignored) records: start, every object deleted/failed/skipped, stops, the typed force confirmation, the forced delete and its result, the final move to the trash, and "local records only" deletes with the tenants left on the VMS. It lives outside the site folders because those move to the trash. 🗒 Delete log on the 📁 Test site tab shows it newest first; force deletes are in bold red.

## Delete site keeps the local records ("vms record deleted")

- The test results are the point of a site, so 🗑 Delete site no longer moves the folder to `sites/.trash/`. It deletes the tenant's objects on the VMS, then sets `status: vms record deleted` (+ `vms_deleted_at`) in `site.yaml` and `status: vms record deleted` (+ `deleted_at`, `objects: []`) on the tenant in `tenants.yaml`. Configuration, tenants.yaml, saved credentials and results stay.
- "vms record deleted" is treated like "deleted" (🧹 Cleanup) everywhere a tenant must be on the VMS: tenant counts, Verify/limits, the Tenants tab. The site list shows a "VMS record deleted" tag instead of Delete site; the tag disappears if a new tenant is onboarded in that site.
- If the site is open, it stays open; the onboarding run state is reset because its objects no longer exist.
- The "Delete local records only" fallback (when the VMS can't be reached) was removed. `DELETE /api/sites` (move to trash) still exists in the API but the UI no longer uses it; remove a site folder by hand if it is really no longer needed.

## Delete site: the tenant's local provider (2026-11-26)

- Seen on VMS 5.5: deleting a tenant leaves its local provider (`provider-<tenant>`, unassigned) behind. `/localproviders/` objects have no tenant field; the only link is the tenant's `local_provider_id`.
- The scan reads `GET /localproviders/{tenant.local_provider_id}/` and includes it only when it is not `default` (id 1), no other tenant uses it, and `assigned_tenants_preview` is empty or the tenant's own name.
- First tried before the tenant; on the real VMS that fails with HTTP 409 "It is forbidden to delete VAST provider …" while the tenant exists. So it is now the last step, after the tenant (also after a force delete), with a fresh check that no tenant uses it. Force delete ignores the provider when checking "nothing else left".
- Tenant already gone: the scan looks for an unassigned provider named exactly `provider-<tenant>` that no tenant uses, shows it in the review and deletes it. Anything else is left alone.
