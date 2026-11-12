#!/usr/bin/env python3
"""
Flatexporter (Playwright Edition)
Automated depot (portfolio) statement exporter for flatex.de.

Supports modern flatex authentication with Two-Factor Authentication (2FA / SCA)
and exports holdings (Bestand) for both the main Depot and Whitebox accounts.
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
        description="Securely export your flatex depot and Whitebox holdings (Bestand) as CSV or JSON."
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
        "-a", "--account",
        choices=["all", "depot", "whitebox"],
        default="all",
        help="Account to export: 'depot', 'whitebox', or 'all' (default: all)"
    )
    parser.add_argument(
        "-o", "--output",
        default="portfolio",
        help="Output base name or file path (default: 'portfolio' -> portfolio_depot.csv / portfolio.json)"
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


def trigger_csv_export(page, headless=False):
    """Triggers the CSV download from the current table page using the Aktionen dropdown."""
    download = None
    try:
        with page.expect_download(timeout=60000) as download_info:
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
                        cb.first.click()
                        time.sleep(0.8)

                        # Look for the CSV option in the expanded dropdown
                        csv_candidates = [
                            page.locator("[id*='entriesI1I']"),
                            page.locator("[id*='entries'][id*='1']"),
                            page.get_by_text("CSV-Export"),
                            page.get_by_text("CSV Export"),
                            page.get_by_text("CSV", exact=True)
                        ]
                        for entry in csv_candidates:
                            if entry.count() > 0 and entry.first.is_visible():
                                print("[*] Clicking 'CSV-Export' entry...", file=sys.stderr)
                                entry.first.click()
                                triggered = True
                                break
                        if triggered:
                            break
                    except Exception as e:
                        print(f"[*] Note: Combobox click attempted ({e})", file=sys.stderr)

            # 2. If not triggered via dropdown, look for direct buttons/links
            if not triggered:
                export_selectors = [
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
                        elem.first.click()
                        triggered = True
                        break

            # 3. If in headed mode, let user click the export button manually if needed
            if not triggered and not headless:
                print("[!] Please click 'Aktionen' -> 'CSV-Export' in the browser window now...", file=sys.stderr)

            download = download_info.value
    except PlaywrightTimeoutError:
        print("Error: Timed out waiting for file download to trigger.", file=sys.stderr)
        return None

    # Retrieve downloaded file content
    temp_download_path = download.path()
    with open(temp_download_path, "r", encoding="latin-1", errors="replace") as f:
        return f.read()


def navigate_and_export_bestand(page, account_name, row_keywords, headless=False):
    """
    On the overview landing page, finds the row matching keywords (e.g. ['Depot'] or ['Whitebox', 'Anlage']),
    opens its row action menu, clicks 'Bestand', and exports the CSV.
    """
    print(f"[*] Locating holdings ('Bestand') for: {account_name}...", file=sys.stderr)

    # Find the row containing any of the keywords
    target_row = None
    for kw in row_keywords:
        matching_rows = page.locator("tr").filter(has_text=kw)
        if matching_rows.count() > 0:
            target_row = matching_rows.first
            break

    if not target_row or target_row.count() == 0:
        # Fallback to div / text matches
        for kw in row_keywords:
            matching_divs = page.locator(f"div:has-text('{kw}'), table tr:has-text('{kw}')")
            if matching_divs.count() > 0:
                target_row = matching_divs.first
                break

    if not target_row or target_row.count() == 0:
        print(f"[!] Warning: Could not find account row for '{account_name}'.", file=sys.stderr)
        return None

    target_row.scroll_into_view_if_needed()
    time.sleep(0.3)

    # Check if 'Bestand' is already visible; if not, click the row dropdown arrow
    bestand_button = page.get_by_text("Bestand", exact=True)
    if not (bestand_button.count() > 0 and bestand_button.first.is_visible()):
        # Find the dropdown trigger (arrow button on the right of the row)
        arrow_trigger = target_row.locator(
            "button, [class*='arrow'], [class*='Combobox'], [class*='combobox'], [class*='select'], [class*='action'], [class*='Toggle'], a"
        ).last
        if arrow_trigger.count() > 0:
            print(f"[*] Opening action menu for {account_name}...", file=sys.stderr)
            arrow_trigger.click()
            time.sleep(0.6)
        else:
            print(f"[!] Warning: Could not locate action arrow for '{account_name}'.", file=sys.stderr)
            return None

    # Click 'Bestand'
    bestand_item = page.get_by_text("Bestand", exact=True).first
    if bestand_item.count() > 0 and bestand_item.is_visible():
        print(f"[*] Opening {account_name} Bestand...", file=sys.stderr)
        bestand_item.click()
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(1)
    else:
        print(f"[!] Warning: 'Bestand' option not found in menu for '{account_name}'.", file=sys.stderr)
        return None

    # Export CSV from the holdings page
    print(f"[*] Exporting CSV for {account_name}...", file=sys.stderr)
    csv_data = trigger_csv_export(page, headless=headless)
    return csv_data


def return_to_overview(page, overview_url):
    """Returns to the main overview landing page."""
    print("[*] Returning to account overview...", file=sys.stderr)
    overview_link = page.locator("a:has-text('Übersicht'), a:has-text('Finanzübersicht'), #header_lnkOverview")
    if overview_link.count() > 0 and overview_link.first.is_visible():
        try:
            overview_link.first.click()
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)
            return
        except Exception:
            pass
    page.goto(overview_url, wait_until="networkidle")
    time.sleep(1)


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
        overview_url = page.url

        exported_results = {}

        # 1. Export Depot Bestand
        if args.account in ("all", "depot"):
            depot_csv = navigate_and_export_bestand(
                page, "Depot", ["Depot"], headless=args.headless
            )
            if depot_csv:
                exported_results["depot"] = depot_csv

        # 2. Export Whitebox Bestand
        if args.account in ("all", "whitebox"):
            if args.account == "all":
                return_to_overview(page, overview_url)

            whitebox_csv = navigate_and_export_bestand(
                page, "Whitebox", ["flatex wealth", "Whitebox", "Anlage 1", "Anlage"], headless=args.headless
            )
            if whitebox_csv:
                exported_results["whitebox"] = whitebox_csv

        browser.close()

        if not exported_results:
            print("Error: No holdings data was exported.", file=sys.stderr)
            sys.exit(1)

        # Output handling
        clean_base = args.output.replace(".csv", "").replace(".json", "")

        if args.format == "json":
            json_data = {}
            for acct_key, raw_csv in exported_results.items():
                json_data[acct_key] = parse_german_csv(raw_csv)

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
                acct_name, raw_csv = next(iter(exported_results.items()))
                out_file = Path(f"{clean_base}.csv" if args.output != "-" else f"{clean_base}_{acct_name}.csv").resolve()
                if args.output == "-":
                    print(raw_csv)
                else:
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(raw_csv)
                    print(f"[*] Success! Saved {acct_name} holdings to: {out_file}", file=sys.stderr)
            else:
                for acct_name, raw_csv in exported_results.items():
                    out_file = Path(f"{clean_base}_{acct_name}.csv").resolve()
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(raw_csv)
                    print(f"[*] Success! Saved {acct_name} holdings to: {out_file}", file=sys.stderr)


def main():
    args = parse_args()
    username, password = get_credentials(args)
    run_exporter(args, username, password)


if __name__ == "__main__":
    main()
