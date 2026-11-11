# Flatexporter (Modern Playwright & Hardened Shell Edition)

An automated tool to export depot (portfolio) contents as **CSV** or **JSON** from [flatex.de](https://www.flatex.de).

Because Flatex uses a JavaScript-rendered portal protected by Myra Security (WAF) and PSD2 Two-Factor Authentication (2FA / Strong Customer Authentication), this repository provides:
1. **`flatexporter.py` (Recommended):** Modern browser automation using **Playwright**. Handles bot protection, dynamic forms, 2FA app approvals, and exports to either CSV or structured JSON.
2. **`flatexporter.sh`:** Hardened bash script based on the original 2020 version with secure credential and temporary file management.

---

## 1. Quickstart with Playwright (Recommended)

### Prerequisites

Create a virtual environment and install the dependencies:

```bash
cd flatexporter
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Usage

#### Interactive Mode (Default: Opens browser window so you can approve 2FA)
```bash
./venv/bin/python3 flatexporter.py -o portfolio.csv
```
1. You will be prompted for your username and password (password input is masked).
2. Chromium will launch and navigate to `https://konto.flatex.de/`.
3. It fills the credentials and waits for your confirmation in the **Flatex Secure App** on your phone.
4. Once authenticated, it triggers the depot statement export and saves it to `portfolio.csv`.

#### Export as Structured JSON
```bash
./venv/bin/python3 flatexporter.py --format json -o portfolio.json
```
Automatically converts German localized CSV numbers (e.g., `1.234,56 €`) into standardized JSON numbers (`1234.56`).

#### Pipe directly to downstream tools / APIs
```bash
./venv/bin/python3 flatexporter.py --format json -o - | jq .
```

#### Headless Mode (For CI/CD or Background Daemons)
```bash
FLATEX_USER="your_user" FLATEX_PASSWORD="your_password" ./venv/bin/python3 flatexporter.py --headless -o portfolio.csv
```

---

## 2. Command-Line Options (`flatexporter.py`)

| Flag | Description | Default |
| :--- | :--- | :--- |
| `-u`, `--user` | Flatex Username / Account Number (or set `FLATEX_USER`) | Prompted |
| `-p`, `--password` | Flatex Password (or set `FLATEX_PASSWORD`) | Prompted |
| `-o`, `--output` | Destination file path (use `-` for `stdout`) | `portfolio.csv` |
| `--format` | Output format: `csv` or `json` | `csv` |
| `--headless` | Run in headless mode (omitted = opens visible browser) | `False` |
| `--timeout` | Timeout in seconds to wait for 2FA approval | `180` |

---

## 3. Two-Factor Authentication (2FA / SCA) Note

Under European PSD2 banking regulations, flatex requires Strong Customer Authentication. When running in headed mode (the default), you can observe the browser window and confirm any push notification in your flatex Secure App. The script will automatically detect the approved session and proceed with the download.

---

## Security & Privacy

* Credentials are never logged to disk or stored in shell history.
* All generated files (`portfolio.csv`, `portfolio.json`, `venv/`, `.env`) are ignored via `.gitignore` to prevent accidental commits.

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE) (GPLv3).
Original author: [Andreas Hartmann](https://github.com/andreas-hartmann/flatexporter).
Modern enhancements & Playwright port: [missunderstoodninja](https://github.com/missunderstoodninja/flatexporter).
