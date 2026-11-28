# Changelog

The app's version is in [`VERSION`](VERSION) and shows next to the title in the app's top bar.
Bump it with every change you push: **major** for changes that break existing sites or configs,
**minor** for new features, **patch** for fixes and docs.

## 1.6.0
- **vperfcheck** (tab ②, **Presets → Vast**): VAST's profile-driven elbencho wrapper, run from one client (the
  coordinator) against all the ticked clients together, for the file system and S3.
  - **⚙ Configure vperfcheck** window with all the settings, the command line, Pre-check / Dry run / Run / Stop, and
    **💾 Save**: kept in the browser and on the site, and logged in 🗒 Logs (`vperfcheck-config`, with the command line).
    Closing with unsaved changes asks first.
  - **⤓ Export configuration / ⤒ Import site** carry the saved vperfcheck settings (not the lab's run state or any
    S3 key). The browser takes the open site's saved settings, so switching or importing a site no longer shows the
    previous site's vperfcheck settings.
  - **⤓ Install / update** onto the shared VAST view; **✓ Pre-check** of everything it needs.
  - **🔑 Set up SSH trust** from the coordinator to the clients (dedicated key, marked `~/.ssh/config` block; existing
    keys, settings and password logins are kept), **🔌 Test SSH between clients**, **Remove SSH trust**.
  - **▶ Run / Dry run / ■ Stop** with the dataset (auto / existing / fixed size), profiles, time, threads, loop,
    latency and advanced options; the command line is shown first. The elbencho services are stopped after every run.
  - **S3**: the keys onboarding created (or a typed pair, used once), the tenant VIP pool as endpoints, the view's bucket.
    The secret is masked in the console and never logged.
  - Results (one row per profile, with latency percentiles) in Performance results, 🗒 Logs (json / txt / csv), Chart
    and the PowerPoint / Word reports. **📦 Package results** downloads vperfcheck's package.
  - Guards for vperfcheck 2026-10-02's limits: "use the existing dataset" only for the target it laid out last, Auto
    size for the first run on an empty S3 prefix, a warning when clients log in as different users.
- **Packages moved into Presets → Client Cmds** (the Required packages section is gone): Check packages, Show last
  check, Install missing, Update packages, and the config's `section: packages` presets (Check VAST driver version).
  - The results open in a **window** when a check finishes (Esc / ✕ closes it; Show last check opens it again). Its
    Install / Reinstall / Uninstall buttons close it so the consoles stay visible, and it opens again after the re-check.
  - The run log (🗒 Logs, `.txt`) now has a readable **PACKAGES** table per client (ok / MISSING, version, updates).
- New config section `vperfcheck:`; `jq` added to `packages.system` and `aws-cli` (AWS CLI v2) to Required packages.
- **Tester manual** (`VAST Test Bench - Tester Manual.pptx`) updated for 1.5 and 1.6: Disconnect all, Client Cmds →
  Packages and the Required packages window, the Vast menu, Configure vperfcheck, SSH trust, the vperfcheck flow,
  export of the vperfcheck settings and two new troubleshooting rows (44 slides).

## 1.5.0
- **`packages.system`** in the config: a plain list of OS packages (e.g. `curl`, `numactl`, `sysstat`, `ethtool`,
  `pciutils`) to check and install on the clients. Each is checked in the client's package database (`dpkg-query` /
  `rpm -q`, so library packages without a command are found too) and installed or updated with the client's package
  manager, together with the required packages. Use `{ name: x, package: { apt: a, dnf: b } }` when the name differs
  per distribution. Edit it in ⚙ Configuration → packages → system (one per line). New sites start with a suggested
  list; existing sites got an empty one.
- The **Required packages** table now has one row per package and one column per client, so a long list fits.
- **Install / Reinstall / Uninstall each package**: buttons in every cell of the Required packages table act on that
  client; **Install on all / Reinstall on all / Uninstall from all** under the package name act on every ticked client it
  applies to. Reinstall and Uninstall ask first. Packages are checked again afterwards.
  - Repo packages use the package manager (`apt-get install --reinstall` / `apt-get remove`, `dnf reinstall` /
    `dnf remove`, …), set as `reinstall` / `remove` under `package_managers` (built-in defaults for older configs).
  - Packages with their own install script need an `uninstall` command: added for **elbencho** (deletes the static
    binary) and **vastnfs** (purges the driver packages and rebuilds the initramfs; reboot to unload the driver).
    `reinstall` is optional and defaults to the install command.
- **Elbencho Auto Write/Read** idles between the write and the read: `elbencho.idle_before_read_seconds`
  (⚙ Configuration → elbencho; 30 by default, 0 = no idle). The app waits with a countdown in each console
  (nothing runs on the client) and the idle shows in the run log; it replaces the usual pause between commands there.
  Any preset can use an idle step: `- wait: <seconds>` (with an optional `label:`) in its `commands`.
- **⏏ Disconnect all** (Consoles card, next to Open all configured): closes the SSH connection of every console
  after a confirmation. The consoles and their output stay; ↻ Reconnect each one to connect again. A console that is
  running a preset is left connected and named in a message.
- On RHEL-family clients, `packages.system` also counts a package that *provides* the name (`curl-minimal` for `curl`).

## 1.4.0
- **✓ Verify on VMS** opens a window that shows each check live: connect to the VMS, then one GET per object
  onboarding created (tenant, VIP pool, view policy, view, quota, …) with what the VMS returned and how long it took,
  then the result is saved. Read-only. **↻ Verify again** re-runs it.
- **🔄 Restart app** button (top bar, or click the amber version badge): restarts the app with the same test site,
  counts down 5 seconds and reloads the page. Blocked while a test, onboarding or site delete is running.
- **Delete site** now also deletes the tenant's **local provider** (`provider-<tenant>`), which the VMS leaves behind:
  as the last step, right after the tenant (the VMS refuses to delete it while the tenant exists). A provider left behind by an
  earlier delete is found and deleted with **🧹 Check VMS for leftovers** (VMS Record Deleted Sites tab).
- Saved SSH clients: **✎ Edit** (name, host, port, user, password; rename keeps the saved password) and
  **🔌 Test** connection, shown step by step: host name → SSH port → login → a command → sudo.

- **VIP Pool** box lists the VIP IPs: the range step 1 will use, or the pool's live range on the VMS once it exists.
  The IP plan window takes a typed **Start IP – End IP** range; after the pool is created it changes the pool's range
  on the VMS (confirmation; refuses IPs held by another VIP pool or compute cluster).

- A malformed VIP candidate range (e.g. `end: 10.20.250`) now gives a plain message naming the setting - when the
  site opens, when the config is saved, and on **Check VIP ranges** - instead of `AddressValueError`.

- Password and token fields are masked text fields instead of browser password fields, so Chrome no longer shows
  "password found in a data breach" or offers to save the VMS / SSH passwords. 👁 still shows and hides them.

- **📊 PowerPoint performance report** from the performance runs you pick (highlights, throughput / IOPS per run,
  per-client throughput, all results, test setup), in the tester manual's template. Saved in results and listed in
  🗒 Logs with a download button. Needs `python-pptx` (added to requirements.txt; installed on the next start).

- Performance report: **Test Architecture**, **Estimated vs Actual** (targets typed in the report window, saved per site),
  **Cluster Performance** (VMS analytics per run window), **Latency** (P50 / P99 / P99.9 from elbencho `--lat --latpercent`);
  Throughput per Run shows time taken and P99 latency. Failed runs are not ticked by default.
- Performance report page 2: **Cluster Network Architecture** drawn from the VMS inventory - split (CNodes + DBoxes,
  front-end and back-end switches) or converged (EBoxes) - with counts, DBox model and link speeds; switch names typed
  in the report window.

- **⤓ Export configuration / ⤒ Import site**: a site's whole configuration (config.yaml, site.yaml, tenants.yaml,
  optionally saved passwords and onboarding state) as a .zip without logs; import creates a new site.

- **Word performance report** (with or instead of PowerPoint), laid out like the usual VAST benchmark write-ups, on a
  **report template** uploaded in ⚙ Configuration (logo, header / footer, styles, `{{placeholders}}`, `{{content}}`);
  starter template to download. Needs `python-docx` and `matplotlib` (installed on the next start).

## 1.3.0
- **Delete site** removes the tenant and everything under it from the VMS, in dependency order, in a 4-step modal.
- **Force delete tenant** when the VMS refuses because files are left (type `force delete <tenant>`).
- Local records and test results are kept: the site moves to the **VMS Record Deleted Sites** tab.
- **Delete log** of every VMS delete.
- Version number shown in the top bar.

## 1.2.0
- Presets grouped into dropdown menus.
- **Custom Linux commands** (Configuration tab) with export and import.

## 1.1.1
- Step-by-step tester manual (PPTX).

## 1.1.0
- **Chart** tab, Mount Status and Client Info presets; cleaner onboarding UI.

## 1.0.0
- First release: tenant onboarding, API tests, SSH client consoles, mount and performance tests.
