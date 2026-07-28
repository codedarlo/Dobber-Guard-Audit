# DobberGuard-Audit

**A WMI-native HP endpoint security & compliance auditor, written in Python.**

DobberGuard-Audit queries a HP device's firmware directly — no PowerShell, no HPCMSL, no third-party agent — and reports on the security 
posture of its BIOS: whether a setup password is set, whether Secure Boot and TPM are enabled, and whether virtualization protections are active.
It produces both a live console summary and a shareable HTML report.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [WMI classes used](#wmi-classes-used)
- [Checks performed](#checks-performed)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Sample output](#sample-output)
- [Safety design](#safety-design)
- [Known limitations](#known-limitations)
- [Design decisions](#design-decisions)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)
- [Author](#author)

---

## Why this exists

HP Wolf Security's flagship product line (HP Sure Click, Sure Start, Sure Admin) is built around one idea:
endpoint firmware and hardware state should be continuously verifiable, not just trusted. 
DobberGuard-Audit is a small-scale demonstration of that same principle — an auditable, scriptable check of a device's BIOS-level security posture,
built from first principles against HP's own published interfaces rather than a third-party scanning tool.

## How it works

HP integrates a WMI (Windows Management Instrumentation) provider directly into the BIOS firmware of its commercial devices, 
exposed under the namespace `root\HP\InstrumentedBIOS`. 
HP built this specifically so BIOS settings could be queried and modified **without** a vendor application or manually entering BIOS setup at boot
— it's the same underlying interface that HP's own official tooling (HPCMSL, BIOS Configuration Utility) is built on top of.

Since WMI is COM-based, it's reachable from any language that can speak COM so not just PowerShell.
DobberGuard-Audit connects to it directly from Python using the `wmi` package (built on `pywin32`).

```
Python script
   │
   ▼
wmi package (pywin32 / COM)
   │
   ▼
root\HP\InstrumentedBIOS  (HP's BIOS WMI provider)
   │
   ▼
Actual firmware settings
```

## WMI classes used

|            Namespace       |           Class             |                                      Purpose                                             |
|----------------------------|-----------------------------|------------------------------------------------------------------------------------------|
|          `root\cimv2`      |   `Win32_ComputerSystem`    | Device manufacturer and model                                                            |
|          `root\cimv2`      |         `Win32_BIOS`        | Serial number, installed BIOS version                                                    |
|          `root\cimv2`      |       `Win32_Procesor`      | Fallback virtualization check (see [Known limitations](#known-limitations))              | 
| `root\HP\InstrumentedBIOS` |    `HP_BIOSEnumeration`     | Lists configurable BIOS settings (Secure Boot, TPM, VT-x, etc.) and their current values |
| `root\HP\InstrumentedBIOS` |      `HP_BIOSPassword`      | Reports whether a BIOS setup password is set                                             |
| `root\HP\InstrumentedBIOS` |   `HP_BIOSSettingInterface` | Writes a new value to a BIOS setting (only reached via `--fix`)                          |
|----------------------------|-----------------------------|------------------------------------------------------------------------------------------|


## Checks performed

- **BIOS Setup Password** — set or not set
- **Secure Boot** — enabled or disabled, with optional remediation
- **TPM** — enabled/available or not
- **Virtualization Technology (VT-x)** — enabled or disabled, with a fallback path for devices where HP's BIOS provider doesn't expose this setting directly (common on consumer product lines)

## Requirements

- Windows 10/11, on genuine HP hardware
- Python 3.8+
- `wmi` and `pywin32` packages
- Run as **Administrator** — required for reliable reads and for any write via `--fix`

## Installation

```powershell
pip install wmi pywin32
```

If `pywin32` throws COM-related errors after install, run its one-time post-install step:

```powershell
python -m pywin32_postinstall -install
```

## Usage

```powershell
# Read-only audit, console + HTML report on the Desktop
python DobberGuard_wmi.py

# Save the report to a specific folder
python DobberGuard_wmi.py --report-path "C:\Reports"

# See exactly what failed and why, even if hidden behind a "Warning" in the report
python DobberGuard_wmi.py --verbose

# Attempt safe remediation (e.g. enabling Secure Boot) - requires BOTH flags
python DobberGuard_wmi.py --fix --yes-i-am-sure
```

`--fix` alone does nothing without `--yes-i-am-sure` — see [Safety design](#safety-design) for why.

## Sample output

```
=== DobberGuard-Audit (WMI-native): HP Endpoint Security Compliance Check ===
[INFO] Device Manufacturer: HP
[INFO] Device Model: HP Pavilion x360 Convertible 14-dw0xxx
[INFO] Serial Number: 5CG0523GZL
[INFO] Current BIOS Version: F.26
[PASS] BIOS Setup Password: A BIOS setup password is set.
[FAIL] Secure Boot: Secure Boot Configuration: Disable
[PASS] TPM: TPM Device: Available
[PASS] Virtualization Technology (VT-x): Virtualization: Enabled

Report written to: C:\Users\<you>\Desktop\DobberGuard-Audit-WMI_22-07-2026_143000.html
```

The HTML report mirrors this with colour-coded rows (green/red/yellow) for quick visual scanning.

## Safety design

Firmware writes are not casually reversible, so this project deliberately adds friction rather than convenience around anything that changes a setting:

- **`--fix` alone changes nothing.** A second, explicitly-named flag, `--yes-i-am-sure`, is required before any write is attempted. Two independent flags are much harder to trigger by accident than one.
- **The previous value is logged before any write**, both to the console and the report, so there's a record of what changed if something needs to be reversed manually.
- **The script never attempts to guess or supply a BIOS setup password.** If a setting requires one to change, the write fails and is reported as a warning rather than silently retried.
- **Read failures fail loud, not silent.** Every caught exception is recorded (see `--verbose`) rather than discarded, so a "Warning" in the report always has a traceable cause behind it.

## Known limitations

- **BIOS/firmware currency** (installed version vs latest available) is **not checked**. That data lives in HP's Softpaq catalog, which is only reachable through HPCMSL or HP Image Assistant — not exposed via WMI. Rather than fake this check, it's reported as an explicit "not checked" line.
- **Consumer-line HP devices** (e.g. Pavilion, Envy) often expose a smaller set of configurable BIOS settings than business-line devices (EliteBook, ProBook, Elite Desktop). HP's own documentation confirms WMI/BIOS management support was built primarily for managed business systems from around 2006–2008 onward, and did not originally extend to consumer or entry-level units. In practice this means a setting like VT-x may not appear under `HP_BIOSEnumeration` at all on a consumer device — this script falls back to the more general `Win32_Processor.VirtualizationFirmwareEnabled` property in that case, which is reported by Windows itself rather than HP's provider.
- **Exact BIOS setting names vary by model.** The script matches settings by keyword rather than exact string, to tolerate this variation, but this means it's possible (though logged, if it happens) for a fuzzy match to catch more than one candidate setting.

## Design decisions

**Why WMI directly, instead of wrapping HPCMSL/PowerShell?**
HPCMSL is a PowerShell module built on top of the same underlying WMI interface. Since WMI is COM-based and language-agnostic, calling it directly from Python removes an entire layer (Python → subprocess → PowerShell → HPCMSL → WMI becomes Python → WMI) with fewer points of failure and no dependency on PowerShell's own module-installation quirks.

**Why the `wmi` package over PowerShell's CIM cmdlets?**
CIM cmdlets (`Get-CimInstance`) are Microsoft's modern, WSMan-based replacement for the legacy WMI cmdlets (`Get-WmiObject`), and are the right choice for **remote, cross-platform, or fleet-wide** management, since WSMan is firewall-friendly in a way the older DCOM transport isn't. This project only ever queries the local machine it's running on, so that advantage doesn't apply here — the `wmi` Python package (COM/DCOM-based, same transport family as the legacy WMI cmdlets) is a reasonable fit for a single-device audit. If this project were extended to audit a fleet of machines remotely, that would be the point to revisit this choice.

**Why fuzzy name-matching instead of exact setting names?**
BIOS setting names are not standardized across HP's product range — the same logical setting can appear under slightly different names on different models. Keyword-based matching trades a small risk of ambiguity (mitigated by logging when more than one setting matches) for working across a much wider range of hardware.

**Why the fallback to `Win32_Processor` for virtualization?**
Given the known gap in HP's consumer-line BIOS WMI support, failing outright when `HP_BIOSEnumeration` doesn't expose a VT-x setting would leave a real, checkable fact (is virtualization enabled?) unreported. Falling back to Windows' own general-purpose processor WMI class is a graceful-degradation pattern: try the most specific source first, fall back to a more universal one rather than giving up.

## Project structure

```
DobberGuard-Audit/
├── DobberGuard_wmi.py     # Main script
└── README.md              # This file
```

## Roadmap

- Extend fleet-wide auditing via remote CIM sessions (WSMan) rather than local-only COM
- Add BIOS/firmware currency checking via the HPCMSL Softpaq catalog as an optional, PowerShell-backed supplementary check
- Export report data as JSON/CSV alongside HTML, for feeding into a dashboard or ticketing system

## Author

**Darlington Huusfeldt**
Cyber Security BSc (Hons) student, Cambridge, UK.
