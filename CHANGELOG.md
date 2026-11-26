# Changelog

The app's version is in [`VERSION`](VERSION) and shows next to the title in the app's top bar.
Bump it with every change you push: **major** for changes that break existing sites or configs,
**minor** for new features, **patch** for fixes and docs.

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
