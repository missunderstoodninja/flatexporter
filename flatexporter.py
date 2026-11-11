#!/usr/bin/env python3
"""
Flatexporter (Playwright Edition)
Automated depot (portfolio) statement exporter for flatex.de.

Supports modern flatex authentication with Two-Factor Authentication (2FA / SCA).
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
        description="Securely export your flatex depot statement as CSV or JSON using Playwright."
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
        "-o", "--output",
        default="portfolio.csv",
        help="Output file path (default: portfolio.csv, use '-' for stdout)"
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


def convert_german_csv_to_json(csv_text):
    """Parse German localized semicolon-delimited CSV into clean structured JSON."""
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
    return json.dumps(records, indent=2, ensure_ascii=False)


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
            # Check if login form is gone or we have moved into the banking portal
            current_url = page.url.lower()
            if "loginformaction" not in current_url and "login" not in current_url:
                login_success = True
                break

            # Or check if authenticated elements are present
            authenticated_selector = page.locator("a:has-text('Abmelden'), button:has-text('Abmelden'), #header_lnkLogout")
            if authenticated_selector.count() > 0:
                login_success = True
                break

            # Check if there is an explicit error message on screen
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

        print("[*] Login successful! Navigating to depot statement...", file=sys.stderr)
        page.wait_until = "networkidle"

        # Navigate or locate the Depot section if not already on it
        depot_link = page.locator("a:has-text('Depotübersicht'), a:has-text('Depotbestand'), a:has-text('Depot')")
        if depot_link.count() > 0 and depot_link.first.is_visible():
            try:
                depot_link.first.click()
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

        print("[*] Triggering CSV export...", file=sys.stderr)

        # Trigger download: Look for standard flatex export buttons, comboboxes, or links
        download = None
        export_selectors = [
            # Combobox option for CSV in flatex classic
            "select[name*='tableAction']",
            "#depositStatementForm_tableActionCombobox",
            "button:has-text('CSV')",
            "a:has-text('CSV')",
            "button:has-text('Export')",
            "a:has-text('Exportieren')",
            "[title*='CSV']",
            "[title*='Export']"
        ]

        try:
            with page.expect_download(timeout=30000) as download_info:
                triggered = False
                for sel in export_selectors:
                    elem = page.locator(sel)
                    if elem.count() > 0 and elem.first.is_visible():
                        if elem.first.evaluate("el => el.tagName.toLowerCase()") == "select":
                            # Try selecting CSV option
                            try:
                                elem.first.select_option(label="CSV")
                                triggered = True
                                break
                            except Exception:
                                pass
                        else:
                            elem.first.click()
                            triggered = True
                            break

                if not triggered:
                    # If in headed mode, let user click the export button manually
                    if not args.headless:
                        print("[!] Automatic export button click could not locate button.", file=sys.stderr)
                        print("[!] Please click 'CSV Export' in the open browser window now...", file=sys.stderr)
                    else:
                        print("Error: Could not locate export button in headless mode.", file=sys.stderr)
                        browser.close()
                        sys.exit(1)

                download = download_info.value
        except PlaywrightTimeoutError:
            print("Error: Timed out waiting for file download to trigger.", file=sys.stderr)
            browser.close()
            sys.exit(1)

        # Retrieve downloaded file content
        temp_download_path = download.path()
        with open(temp_download_path, "r", encoding="latin-1", errors="replace") as f:
            raw_content = f.read()

        browser.close()

        # Format and output the data
        if args.format == "json":
            output_data = convert_german_csv_to_json(raw_content)
        else:
            output_data = raw_content

        if args.output == "-":
            print(output_data)
        else:
            out_path = Path(args.output).resolve()
            with open(out_path, "w", encoding="utf-8") as out_file:
                out_file.write(output_data)
            print(f"[*] Success! Portfolio statement exported to: {out_path}", file=sys.stderr)


def main():
    args = parse_args()
    username, password = get_credentials(args)
    run_exporter(args, username, password)


if __name__ == "__main__":
    main()
