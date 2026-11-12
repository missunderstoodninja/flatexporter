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
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    print("Error: 'playwright' is not installed in the active environment.", file=sys.stderr)
    print("Please install it with:\n  pip install -r requirements.txt\n  playwright install chromium", file=sys.stderr)
    sys.exit(1)

LOGIN_URL = "https://konto.flatex.de/"

# DOM Selectors for flatex login form
USER_SELECTOR = "#loginForm_txtUserId"
PASS_SELECTOR = "#loginForm_txtPassword_txtPassword"
SUBMIT_SELECTOR = "#loginForm_btnLogin"


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
            print("\nAborted.", file=sys.stderr)
            sys.exit(1)

    if not password:
        try:
            password = getpass.getpass("Flatex Password: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.", file=sys.stderr)
            sys.exit(1)

    if not username or not password:
        print("Error: Both username and password are required.", file=sys.stderr)
        sys.exit(1)

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
                        print("[*] Found 'Aktionen' dropdown, opening menu...", file=sys.stderr)
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
                                print("[*] Clicking 'Export CSV' entry...", file=sys.stderr)
                                entry.first.click(force=True)
                                triggered = True
                                break
                        if triggered:
                            break
                    except Exception as e:
                        print(f"[*] Note: Combobox click attempted ({e})", file=sys.stderr)

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
                        print("[*] Clicking direct export element...", file=sys.stderr)
                        elem.first.click(force=True)
                        triggered = True
                        break

            # 3. If in headed mode, let user click the export button manually if needed
            if not triggered and not headless:
                print("[!] Please click 'Aktionen' -> 'Export CSV' in the browser window now...", file=sys.stderr)

            download = download_info.value
    except PlaywrightTimeoutError:
        print("Error: Timed out waiting for file download to trigger.", file=sys.stderr)
        return None

    # Retrieve downloaded file content
    temp_download_path = download.path()
    with open(temp_download_path, "r", encoding="latin-1", errors="replace") as f:
        return f.read()


def navigate_to_depot_holdings(page):
    """
    Ensures the browser navigates from the overview page to the Depotbestand page.
    """
    dismiss_cookiebot(page)

    # Check if already on the Depotbestand page
    if page.locator("#depositStatementForm, [id*='depositStatement']").count() > 0:
        return True

    print("[*] Locating Depot row on overview table...", file=sys.stderr)
    depot_row = page.locator("tr").filter(has_text="Depot").first
    if not depot_row or depot_row.count() == 0:
        depot_row = page.locator("div:has-text('Depot')").first

    if not depot_row or depot_row.count() == 0:
        print("[!] Warning: Could not find Depot row on the overview screen.", file=sys.stderr)
        return False

    depot_row.scroll_into_view_if_needed()
    time.sleep(0.3)

    # Check if 'Bestand' is already visible; if not, click the row dropdown arrow
    bestand_button = page.get_by_text("Bestand", exact=True)
    if not (bestand_button.count() > 0 and bestand_button.first.is_visible()):
        arrow_trigger = depot_row.locator(
            "button, [class*='arrow'], [class*='Combobox'], [class*='combobox'], [class*='select'], [class*='action'], [class*='Toggle'], a"
        ).last
        if arrow_trigger.count() > 0:
            print("[*] Opening Depot action dropdown...", file=sys.stderr)
            arrow_trigger.click(force=True)
            time.sleep(0.6)
            dismiss_cookiebot(page)
        else:
            print("[!] Warning: Could not locate Depot dropdown arrow.", file=sys.stderr)
            return False

    # Click 'Bestand'
    bestand_item = page.get_by_text("Bestand", exact=True).first
    if bestand_item.count() > 0 and bestand_item.is_visible():
        print("[*] Opening Depotbestand (stocks & ETFs)...", file=sys.stderr)
        bestand_item.click(force=True)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(1)
        dismiss_cookiebot(page)
        return True
    else:
        print("[!] Warning: 'Bestand' option not found in Depot action menu.", file=sys.stderr)
        return False


def navigate_to_overview(page, overview_url):
    """Returns to the main overview landing page."""
    dismiss_cookiebot(page)
    overview_link = page.locator("a:has-text('Übersicht'), a:has-text('Finanzübersicht'), #header_lnkOverview")
    if overview_link.count() > 0 and overview_link.first.is_visible():
        try:
            overview_link.first.click(force=True)
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)
            dismiss_cookiebot(page)
            return
        except Exception:
            pass
    page.goto(overview_url, wait_until="networkidle")
    time.sleep(1)
    dismiss_cookiebot(page)


def run_exporter(args, username, password):
    with sync_playwright() as p:
        print("[*] Launching browser...", file=sys.stderr)
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

        print(f"[*] Navigating to {LOGIN_URL} ...", file=sys.stderr)
        page.goto(LOGIN_URL, wait_until="networkidle")

        # Wait for dynamic login form rendering
        try:
            page.wait_for_selector(USER_SELECTOR, timeout=30000)
        except PlaywrightTimeoutError:
            print("Error: Login form elements did not render within 30 seconds.", file=sys.stderr)
            browser.close()
            sys.exit(1)

        print("[*] Submitting login credentials...", file=sys.stderr)
        page.fill(USER_SELECTOR, username)
        page.fill(PASS_SELECTOR, password)
        page.click(SUBMIT_SELECTOR)

        # Handle 2FA / Session transition
        print(f"[*] Waiting for 2FA approval / login completion (up to {args.timeout}s)...", file=sys.stderr)
        print("[!] Please check and confirm the push notification in your flatex Secure App if prompted.", file=sys.stderr)

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
                    print(f"Error from flatex portal: {err_text}", file=sys.stderr)
                    browser.close()
                    sys.exit(1)

            time.sleep(2)

        if not login_success:
            print("Error: Login was not completed within the timeout period.", file=sys.stderr)
            browser.close()
            sys.exit(1)

        print("[*] Login successful!", file=sys.stderr)
        page.wait_for_load_state("networkidle", timeout=15000)
        dismiss_cookiebot(page)
        overview_url = page.url

        exported_results = {}

        # 1. Export Depot Holdings (Stocks, ETFs)
        if args.type in ("all", "holdings"):
            if navigate_to_depot_holdings(page):
                holdings_csv = trigger_csv_export(page, headless=args.headless)
                if holdings_csv:
                    exported_results["holdings"] = holdings_csv

        # 2. Export Accounts Summary (Cashkonto, CFD, Whitebox, Depot balances)
        if args.type in ("all", "summary"):
            navigate_to_overview(page, overview_url)
            print("[*] Exporting accounts summary (Cash, CFD, Whitebox, Depot totals)...", file=sys.stderr)
            summary_csv = trigger_csv_export(page, headless=args.headless)
            if summary_csv:
                exported_results["summary"] = summary_csv

        browser.close()

        if not exported_results:
            print("Error: No data was exported.", file=sys.stderr)
            sys.exit(1)

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
                print(f"[*] Success! Saved structured JSON to: {out_file}", file=sys.stderr)
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
                    print(f"[*] Success! Saved {key} to: {out_file}", file=sys.stderr)
            else:
                for key, raw_csv in exported_results.items():
                    out_file = Path(f"{clean_base}_{key}.csv").resolve()
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(raw_csv)
                    print(f"[*] Success! Saved {key} to: {out_file}", file=sys.stderr)


def main():
    args = parse_args()
    username, password = get_credentials(args)
    run_exporter(args, username, password)


if __name__ == "__main__":
    main()
