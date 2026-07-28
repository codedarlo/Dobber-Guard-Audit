import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    import wmi
except ImportError:
    print("Missing dependancy. Install with: Pip install wmi pywin32 --break-system-packages")
    sys.exit(1)

HP_NAMESPACE = r"root\HP\InstrumentedBIOS"

# Populated by --verbose; holding (context, exception-string) pairs for anything that failed

DIAGNOSTICS = []

def log_failures(context: str, exc: Exception) -> None:
    DIAGNOSTICS.append((context, str(exc)))

def connection(namespace=None):
    try:
        return wmi.WMI(namespace=namespace) if namespace else wmi.WMI()
    except Exception as exc:
        log_failures(f"connection(namespace={namespace!r}", exc)
        return None

def get_device_info(cimv2):
    model = serial = bios_version = manufacturer = "Unknown"
    if cimv2 is None:
        return manufacturer, model, serial, bios_version
    try:
        cs = cimv2.win32_ComputerSystem()[0]
        model = cs.Model or model
        manufacturer = cs.Manufacturer or manufacturer
    except Exception as exc:
        log_failures("get_device_info: Win32_ComputerSystem", exc)
    try:
        bios = cimv2.Win32_BIOS()[0]
        serial = bios.SerialNumber or serial
        bios_version = bios.SMBIOSBIOSVersion or bios_version
    except Exception as exc:
        log_failures("get_device_info: Win32_bios", exc)
    return manufacturer, model, serial, bios_version


def find_enum_setting(hp, *name_words):
    try:
        settings = hp.HP_BIOSEnumeration()
    except Exception as exc:
        log_failures(f"find_enum_setting{name_words}: HP_BIOSEnumeration", exc)
        return None

    matches = [s for s in settings if all(w.lower() in (s.Name or "").lower() for w in name_words)]
    if len(matches) > 1:
        log_failures(
            f"find_enum_setting{name_words}",
            Exception(f"{len(matches)} settings matched: {[m.Name for m in matches]} - used the first."),
        )
    return matches [0] if matches else None

def find_hp_virtualization(hp, check_name: str, name_words, pass_values=("enable", "enabled"), results=None):
    if results is None:
        results = []

    initial_count = len(results)
    evaluate_bios_setting(hp, check_name, name_words, pass_values=pass_values, results=results)

    if len(results) > initial_count and results[-1][1] == "Warning":
        results.pop()

        try:
            c = wmi.WMI(namespace=r"root\cimv2")
            cpus = c.Win32_Processor()
            is_enabled = any(getattr(cpu, "VirtualizationFirmwareEnabled", False) for cpu in cpus)

            if is_enabled:
                results.append((check_name, "pass", "Virtualization: Enabled"))
            else:
                results.append((check_name, "fail", "Virtualization: Disabled"))

        except Exception as err:
            results.append((check_name, "Waring", f"HP WMI missing key and fallback query failed: {err}"))
    return results

def evaluate_bios_setting(hp, check_name: str, name_words, pass_values=("enable", "enabled"), allow_fix=False, args=None, results=None):
    setting = find_enum_setting(hp, *name_words)
    if not setting:
        results.append((check_name, "Warning", "Setting not found under HP_BIOSEnumeration for this model."))
        return

    value = setting.CurrentValue or setting.Value or ""
    is_pass = any(pv.lower() in value.lower() for pv in pass_values)

    if is_pass:
        results.append((check_name, "pass", f"{setting.name}: {value}"))
        return

    results.append((check_name, "pass", f"{setting.name}: {value}"))

    if allow_fix and args and args.fix:
        attempt_fix(hp, setting, target_value="Enable", check_name=check_name, args=args, results=results)

def attempt_fix(hp, setting, target_value: str, check_name: str, args, results) -> None:
    if not args.yes_i_am_sure:
        results.append((
            f"{check_name} (Remediation)", "Info",
            "skipped: --fix was set but --yes-i-am-sure was not. No firmware write attempted.",
        ))
        return

    previous_value = setting.CurrentValue or setting.Value or "Unknown"
    print(f" -> about to change '{setting.Name}' from '{previous_value}' to '{target_value}'")

    try:
        iface = hp.HP_BIOSSettingInterface()[0]
        result_code = iface.setBIOSSetting(setting.Name, target_value)
        results.append((
            f"{check_name} (Remediation)", "info", f"Changed '{setting.Name}' from '{previous_value} to '{target_value}'. Return code: {result_code}. "
            f"A reboot may be required for this to take effect.",
        ))
    except Exception as exc:
        log_failures(f"attempt_fix {setting.Name}", exc)
        results.append((
            f"{check_name} (Remediation)", "warning",
            f"Auto-fix failed, likeley requires a BIOS setup password: {exc}",
        ))

def check_setup_password(hp, results) -> None:
    try:
        passwords = hp.HP_BIOSPassword()
        setup_pw = next((p for p in passwords if "setup" in (p.Name or "").lower()), None)
        if setup_pw is None:
            results.append(("BIOS SETUP PASSWORD", "Warning", "Setup Password instance not found under HP_BIOSPassword."))
            return
        if int(setup_pw.IsSet):
            results.append(("BIOS Setup Password", "Pass", "A BIOS setup password is set."))
        else:
            results.append((
            "BIOS Setup Password", "Fail",
            "no BIOS setup password set. Firmware setting can be changed by anyone with physical access. ",))
    except Exception as exc:
        log_failures("check_setup_password", exc)
        results.append(("BIOS setup Password", "Warning", f"could not read setting: {exc}"))


def build_report_html(results, model, serial, timestamp) -> str:
    row_colors = {"Pass": "#d4edda", "Fail": "#f8d7da", "Warning": "#fff3cd", "Info": "#e2e3e5"}

    def row_html(check, status, detail):
        color = row_colors.get(status, "#ffffff")
        return f"<tr style='background:{color}'><td>{check}</td><td>{status}</td><td>{detail}</td></tr>"

    rows_html = "\n".join(row_html(c, s, d) for c, s, d in results)

    return f"""<html>
<head><title>DobberGaurd_Audit (WMI) Report - {timestamp}</title>
<style> 
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 30px; }}
    h1 {{ color: #0096d6; }}
    table {{ border-collapse: collapse; width: 100%; }}
    td, th {{ border: 1px solid #ccc; padding: 8px 12px; text-align: left; }}
    th {{background: #0096d6; color: white; }}
</style></head> 
<body> 
    <h1>DobberGaurd-Audit Endpoint Compliance Report (WMI-native)</h1> 
    <p>Generated: {timestamp} | Model: {model} | Serial: {serial}</p> 
    <table>
    <tr><th>Check</th><th>Status</th><th>Detail</th></tr>
    {rows_html}
    </table>
</body></html>
"""

def main () -> int:
    parser = argparse.ArgumentParser(description="DobberGaurd-Audit (WMI-native) - HP Device security compliance auditor")
    parser.add_argument("--report-path", default=str(Path.home() / "Desktop"))
    parser.add_argument("--fix", action="store_true", help="Attempt to remediate safe settings (e.g enable Secure Boot) via HP_BIOSSettingInterface. "
                        "Requires --yes-i-am-sure as well; never attempts settings that need a BIOS setup password", )
    parser.add_argument("--yes-i-am-sure", action="store_true",
                        help="Required alongside --fix to actually write to firmware. Seprate flag on purpose - firmware"
                        "writes are not reversible",)
    parser.add_argument("--verbose", action="store_true", help="connenction errors, missing WMI properties,"
                        "that are otherwise hidden behind a warning status in the report ", )

    args = parser.parse_args()

    if sys.platform != "win32":
        print(r"This tool requires Windows with HP's WMI BIOS provider (root\HP\InstrumentedBIOS).")
        return 1

    print("=== DobberGaurd-Audit (WMI-native) HP Device Security Compliance Check===")

    cimv2 = connection()
    hp = connection(HP_NAMESPACE)
    if hp is None:
        print(r"Could not connect to root\HP\InstrumentedBIOS. This WMI namespace is only present on HP"
              "commercial hardware with the HP BIOS WMI provider installed. ")
        if args.verbose:
            _print_diagnostics()
        return 1

    manufacturer, model, serial, bios_version = get_device_info(cimv2)
    if "hp" not in manufacturer.lower() and "hewlett" not in manufacturer.lower():
        print(f"Manufacturer reports as '{manufacturer}, not HP. Results may not be meaningful.")

    results = []
    results.append(("Device Manufacturer", "Info", manufacturer))
    results.append(("Device Model", "Info", model))
    results.append(("Serial Number", "Info", serial))
    results.append(("Current BIOS Version", "Info", bios_version))

    check_setup_password(hp, results)

    evaluate_bios_setting(hp, "Secure Boot", ("Secure", "Boot"), allow_fix=True, args=args, results=results)
    evaluate_bios_setting(hp, "TPM",("tpm",), pass_values=("enable", "available"), args=args, results=results)
    find_hp_virtualization(hp,"Virtualization Technology (VT-x)",("Virtualization Technology", "VTx", "SVM mode"
    , "Intel Virtualization Technology"),pass_values=("enable", "enabled"), results=results)

    print()
    for check, status, detail in results:
        print(f"[{status.upper()}] {check}: {detail}")

    if args.verbose:
        _print_diagnostics()

    timestamp = datetime.now().strftime("%d-%m-%Y_%H%M%S")
    report_dir = Path(args.report_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"DobberGaurd-Audit-WMI_{timestamp}.html"
    report_file.write_text(build_report_html(results, model, serial, timestamp), encoding="utf-8")

    print(f"\nReport written to: {report_file}")
    return 0

def _print_diagnostics() -> None:
    if not DIAGNOSTICS:
        print("/n[diagnostics] No swallowed exceptions recorded.")
        return
    print(f"\n[diagnostics] {len(DIAGNOSTICS)} swallowed exception(s) recorded:")
    for context, message in DIAGNOSTICS:
        print(f"  -  {context}: {message}")


if __name__ == "__main__":
    sys.exit(main())


