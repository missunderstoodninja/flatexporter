#!/usr/bin/env python3
"""
Flatexporter (Playwright Edition)
Automated portfolio & account statement exporter for flatex.de.

Exports:
1. Depot Holdings (stocks, ETFs, individual positions)
2. Accounts Summary (Cashkonto, CFD-Transferkonto, Whitebox totals)
"""

import argparse
import csv
import getpass
import json
import os
import sys
import time
import traceback
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    print("Error: 'playwright' is not installed in the active environment.", file=sys.stderr)
    print("Please install it with:\n  pip install -r requirements.txt\n  playwright install chromium", file=sys.stderr)
    sys.exit(1)

LOGIN_URL = "https://konto.flatex.de/"
LOG_FILE = Path("flatexporter.log").resolve()

# DOM Selectors for flatex login form
USER_SELECTOR = "#loginForm_txtUserId"
PASS_SELECTOR = "#loginForm_txtPassword_txtPassword"
SUBMIT_SELECTOR = "#loginForm_btnLogin"


def log(msg):
    """Prints status messages to stderr and appends them to flatexporter.log."""
    print(msg, file=sys.stderr, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def pause_and_exit(code=0):
    """Prevents terminal window from instantly closing on macOS if the shell exits."""
    if sys.stdin.isatty():
        try:
            input("\n[Process completed. Press Enter to exit...]")
        except (KeyboardInterrupt, EOFError):
            pass
    sys.exit(code)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Securely export your flatex depot holdings (stocks & ETFs) and accounts summary (Cash, CFD, Whitebox)."
    )
    parser.add_argument(
        "-u", "--user",
        help="Flatex Username / Account Number (or set FLATEX_USER env var)"
    )
    parser.add_argument(
        "-p", "--password",
        help="Flatex Password (or set FLATEX_PASSWORD env var; omitted for hidden prompt)"
    )
    parser.add_argument(
        "-t", "--type",
        choices=["all", "holdings", "summary"],
        default="all",
        help="Data to export: 'holdings' (Depot stocks/ETFs), 'summary' (Cash, CFD, Whitebox balances), or 'all' (default: all)"
    )
    parser.add_argument(
        "-o", "--output",
        default="portfolio",
        help="Output base name or file path (default: 'portfolio' -> portfolio_holdings.csv & portfolio_summary.csv / portfolio.json)"
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format: 'csv' (default) or 'json'"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default: headed, so you can observe 2FA confirmation)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout in seconds to wait for 2FA approval (default: 180s)"
    )
    return parser.parse_args()


def get_credentials(args):
    """Retrieve credentials securely from arguments, environment variables, or interactive prompt."""
    username = args.user or os.environ.get("FLATEX_USER")
    password = args.password or os.environ.get("FLATEX_PASSWORD")

    if not username:
        try:
            username = input("Flatex Username / Account Number: ").strip()
        except (KeyboardInterrupt, EOFError):
            log("\nAborted.")
            pause_and_exit(1)

    if not password:
        try:
            password = getpass.getpass("Flatex Password: ").strip()
        except (KeyboardInterrupt, EOFError):
            log("\nAborted.")
            pause_and_exit(1)

    if not username or not password:
        log("Error: Both username and password are required.")
        pause_and_exit(1)

    return username, password


def parse_german_csv(csv_text):
    """Parse German localized semicolon-delimited CSV into structured records with normalized numbers."""
    reader = csv.DictReader(csv_text.splitlines(), delimiter=";")
    records = []
    for row in reader:
        cleaned = {}
        for key, val in row.items():
            if not key:
                continue
            cleaned_key = key.strip()
            if isinstance(val, str):
                cleaned_val = val.strip()
                # Attempt to normalize German number format (e.g. "1.234,56 €" -> 1234.56)
                num_str = cleaned_val.replace(".", "").replace(",", ".").replace("€", "").strip()
                try:
                    cleaned[cleaned_key] = float(num_str) if "." in num_str else int(num_str)
                except ValueError:
                    cleaned[cleaned_key] = cleaned_val
            else:
                cleaned[cleaned_key] = val
        if cleaned:
            records.append(cleaned)
    return records


def dismiss_cookiebot(page):
    """Dismisses and removes Cookiebot banners/underlays to prevent pointer interception."""
    try:
        accept_btn = page.locator(
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll, "
            "#CybotCookiebotDialogBodyButtonAccept, "
            "button:has-text('Alle zulassen'), "
            "button:has-text('Alle akzeptieren')"
        )
        if accept_btn.count() > 0 and accept_btn.first.is_visible():
            accept_btn.first.click(force=True)
            time.sleep(0.3)
    except Exception:
        pass

    # Ensure any invisible or lingering overlay element is removed from DOM
    try:
        page.evaluate("""() => {
            const ids = ['CybotCookiebotDialog', 'CybotCookiebotDialogBodyUnderlay', 'cookie-banner'];
            for (const id of ids) {
                const el = document.getElementById(id);
                if (el) el.remove();
            }
        }""")
    except Exception:
        pass


def trigger_csv_export(page, headless=False):
    """Triggers the CSV download from the current table page using the Aktionen dropdown."""
    dismiss_cookiebot(page)
    download = None

    try:
        with page.expect_download(timeout=45000) as download_info:
            triggered = False

            # 1. Check for Flatex Classic 'Aktionen' dropdown combobox
            combobox_candidates = [
                page.locator("[id*='tableActionCombobox']"),
                page.locator("button:has-text('Aktionen')"),
                page.get_by_text("Aktionen", exact=True),
                page.locator("select[name*='tableAction']")
            ]
            for cb in combobox_candidates:
                if cb.count() > 0 and cb.first.is_visible():
                    try:
                        log("[*] Found 'Aktionen' dropdown, opening menu...")
                        cb.first.click(force=True)
                        time.sleep(0.8)
                        dismiss_cookiebot(page)

                        # Look for the CSV option in the expanded dropdown
                        csv_candidates = [
                            page.locator("[id*='entriesI1I']"),
                            page.locator("[id*='tableActionCombobox_entriesI1I']"),
                            page.get_by_text("Export CSV"),
                            page.get_by_text("CSV-Export"),
                            page.get_by_text("CSV Export"),
                            page.get_by_text("CSV", exact=True)
                        ]
                        for entry in csv_candidates:
                            if entry.count() > 0 and entry.first.is_visible():
                                log("[*] Clicking 'Export CSV' entry...")
                                entry.first.click(force=True)
                                triggered = True
                                break
                        if triggered:
                            break
                    except Exception as e:
                        log(f"[*] Note: Combobox click attempted ({e})")

            # 2. If not triggered via dropdown, look for direct buttons/links
            if not triggered:
                export_selectors = [
                    page.locator("button:has-text('Export CSV')"),
                    page.locator("a:has-text('Export CSV')"),
                    page.locator("button:has-text('CSV')"),
                    page.locator("a:has-text('CSV')"),
                    page.locator("button:has-text('Export')"),
                    page.locator("a:has-text('Exportieren')"),
                    page.locator("[title*='CSV']"),
                    page.locator("[title*='Export']")
                ]
                for elem in export_selectors:
                    if elem.count() > 0 and elem.first.is_visible():
                        log("[*] Clicking direct export element...")
                        elem.first.click(force=True)
                        triggered = True
                        break

            # 3. If in headed mode, let user click the export button manually if needed
            if not triggered and not headless:
                log("[!] Please click 'Aktionen' -> 'Export CSV' in the browser window now...")

            download = download_info.value
    except PlaywrightTimeoutError:
        log("Error: Timed out waiting for file download to trigger.")
        return None

    # Retrieve downloaded file content
    temp_download_path = download.path()
    with open(temp_download_path, "r", encoding="latin-1", errors="replace") as f:
        return f.read()


def navigate_to_depot_holdings(page, headless=False):
    """
    Navigates from the overview page to the Depotbestand page.
    """
    dismiss_cookiebot(page)

    # 1. Check if already on the Depotbestand page
    if page.locator("#depositStatementForm, [id*='depositStatement']").count() > 0:
        log("[*] Already on Depotbestand page.")
        return True

    # 2. Try direct menu links
    log("[*] Navigating to Depotbestand...")
    try:
        direct_link = page.locator("a:has-text('Depotbestand'), #header_lnkDepositStatement, a[href*='depositStatement']")
        if direct_link.count() > 0 and direct_link.first.is_visible():
            direct_link.first.click(force=True)
            time.sleep(1)
            dismiss_cookiebot(page)
            if page.locator("#depositStatementForm, [id*='depositStatement']").count() > 0:
                return True
    except Exception:
        pass

    # 3. Try top menu: Konto & Depot -> Depotbestand
    try:
        konto_depot = page.locator("a:has-text('Konto & Depot'), span:has-text('Konto & Depot')")
        if konto_depot.count() > 0 and konto_depot.first.is_visible():
            konto_depot.first.click(force=True)
            time.sleep(0.5)
            depot_sub = page.locator("a:has-text('Depotbestand')")
            if depot_sub.count() > 0 and depot_sub.first.is_visible():
                depot_sub.first.click(force=True)
                time.sleep(1)
                dismiss_cookiebot(page)
                if page.locator("#depositStatementForm, [id*='depositStatement']").count() > 0:
                    return True
    except Exception:
        pass

    # 4. Fallback to Depot row in overview table
    log("[*] Locating Depot row on overview table...")
    depot_row = page.locator("tr").filter(has_text="Depot").first
    if not depot_row or depot_row.count() == 0:
        depot_row = page.locator("div:has-text('Depot')").first

    if not depot_row or depot_row.count() == 0:
        log("[!] Warning: Could not find Depot row on overview screen.")
        return False

    depot_row.scroll_into_view_if_needed()
    time.sleep(0.3)

    # Check if 'Bestand' popup is already visible
    bestand_button = page.get_by_text("Bestand", exact=True)
    if not (bestand_button.count() > 0 and bestand_button.first.is_visible()):
        # Try clicking any trigger in the last column
        last_cell = depot_row.locator("td").last
        cell_triggers = last_cell.locator("button, a, span, div, svg").all()
        clicked = False
        if cell_triggers:
            for trg in reversed(cell_triggers):
                try:
                    if trg.is_visible():
                        trg.click(force=True)
                        clicked = True
                        break
                except Exception:
                    pass
        if not clicked:
            last_cell.click(force=True)

        time.sleep(0.6)
        dismiss_cookiebot(page)

    # Click 'Bestand' from popup
    bestand_item = page.get_by_text("Bestand", exact=True).first
    if bestand_item.count() > 0 and bestand_item.is_visible():
        log("[*] Opening Depotbestand (stocks & ETFs)...")
        bestand_item.click(force=True)
        time.sleep(1.5)
        dismiss_cookiebot(page)
        return True

    # 5. User assistance if running headed
    if not headless:
        log("[!] Please click 'Bestand' next to your Depot in the browser window now...")
        for _ in range(20):
            time.sleep(1)
            if page.locator("#depositStatementForm, [id*='depositStatement']").count() > 0:
                log("[*] Detected navigation to Depotbestand!")
                return True

    log("[!] Warning: Could not navigate to Depotbestand.")
    return False


def navigate_to_overview(page, overview_url):
    """Returns to the main overview landing page."""
    dismiss_cookiebot(page)
    overview_link = page.locator("a:has-text('Übersicht'), a:has-text('Finanzübersicht'), #header_lnkOverview")
    if overview_link.count() > 0 and overview_link.first.is_visible():
        try:
            overview_link.first.click(force=True)
            time.sleep(1)
            dismiss_cookiebot(page)
            return
        except Exception:
            pass
    try:
        page.goto(overview_url, wait_until="domcontentloaded")
        time.sleep(1)
        dismiss_cookiebot(page)
    except Exception:
        pass


def run_exporter(args, username, password):
    with sync_playwright() as p:
        log("[*] Launching browser...")
        browser = p.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            accept_downloads=True
        )
        page = context.new_page()

        log(f"[*] Navigating to {LOGIN_URL} ...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # Wait for dynamic login form rendering
        try:
            page.wait_for_selector(USER_SELECTOR, timeout=30000)
        except PlaywrightTimeoutError:
            log("Error: Login form elements did not render within 30 seconds.")
            browser.close()
            pause_and_exit(1)

        log("[*] Submitting login credentials...")
        page.fill(USER_SELECTOR, username)
        page.fill(PASS_SELECTOR, password)
        page.click(SUBMIT_SELECTOR)

        # Handle 2FA / Session transition
        log(f"[*] Waiting for 2FA approval / login completion (up to {args.timeout}s)...")
        log("[!] Please check and confirm the push notification in your flatex Secure App if prompted.")

        login_success = False
        start_time = time.time()

        while time.time() - start_time < args.timeout:
            current_url = page.url.lower()
            if "loginformaction" not in current_url and "login" not in current_url:
                login_success = True
                break

            authenticated_selector = page.locator("a:has-text('Abmelden'), button:has-text('Abmelden'), #header_lnkLogout")
            if authenticated_selector.count() > 0:
                login_success = True
                break

            error_box = page.locator(".errorMessage, .error, .alert-danger, #errorMsg")
            if error_box.count() > 0 and error_box.first.is_visible():
                err_text = error_box.first.inner_text().strip()
                if err_text:
                    log(f"Error from flatex portal: {err_text}")
                    browser.close()
                    pause_and_exit(1)

            time.sleep(2)

        if not login_success:
            log("Error: Login was not completed within the timeout period.")
            browser.close()
            pause_and_exit(1)

        log("[*] Login successful!")
        time.sleep(1)
        dismiss_cookiebot(page)
        overview_url = page.url

        exported_results = {}

        # 1. Export Depot Holdings (Stocks, ETFs)
        if args.type in ("all", "holdings"):
            if navigate_to_depot_holdings(page, headless=args.headless):
                holdings_csv = trigger_csv_export(page, headless=args.headless)
                if holdings_csv:
                    exported_results["holdings"] = holdings_csv

        # 2. Export Accounts Summary (Cashkonto, CFD, Whitebox, Depot balances)
        if args.type in ("all", "summary"):
            navigate_to_overview(page, overview_url)
            log("[*] Exporting accounts summary (Cash, CFD, Whitebox, Depot totals)...")
            summary_csv = trigger_csv_export(page, headless=args.headless)
            if summary_csv:
                exported_results["summary"] = summary_csv

        browser.close()

        if not exported_results:
            log("Error: No data was exported.")
            pause_and_exit(1)

        # Output handling
        clean_base = args.output.replace(".csv", "").replace(".json", "")

        if args.format == "json":
            json_data = {}
            for key, raw_csv in exported_results.items():
                json_data[key] = parse_german_csv(raw_csv)

            json_output = json.dumps(json_data, indent=2, ensure_ascii=False)
            if args.output == "-":
                print(json_output)
            else:
                out_file = Path(f"{clean_base}.json").resolve()
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(json_output)
                log(f"[*] Success! Saved structured JSON to: {out_file}")
        else:
            # CSV output
            if len(exported_results) == 1:
                key, raw_csv = next(iter(exported_results.items()))
                out_file = Path(f"{clean_base}.csv" if args.output.endswith(".csv") else f"{clean_base}_{key}.csv").resolve()
                if args.output == "-":
                    print(raw_csv)
                else:
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(raw_csv)
                    log(f"[*] Success! Saved {key} to: {out_file}")
            else:
                for key, raw_csv in exported_results.items():
                    out_file = Path(f"{clean_base}_{key}.csv").resolve()
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(raw_csv)
                    log(f"[*] Success! Saved {key} to: {out_file}")


def main():
    args = parse_args()
    username, password = get_credentials(args)
    try:
        run_exporter(args, username, password)
    except Exception as e:
        log(f"\n[!] Unexpected error: {e}")
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n--- Crash trace at {time.ctime()} ---\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        pause_and_exit(1)


if __name__ == "__main__":
    main()
