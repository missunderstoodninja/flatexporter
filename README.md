# Flatexporter (Hardened Edition)

A secure shell script to export depot (portfolio) contents as CSV from [flatex.de](https://www.flatex.de).

Flatex does not provide an official public API, so this script replicates the authentication and export flow of the web interface.

> Forked from [andreas-hartmann/flatexporter](https://github.com/andreas-hartmann/flatexporter) with security, privacy, and cross-platform enhancements.

---

## Key Improvements in this Fork

* **No Credentials in CLI Arguments:** Protects credentials from leaking in `ps`, `/proc`, terminal logs, and shell history (`~/.bash_history` / `~/.zsh_history`).
* **Interactive Masked Input:** Prompts for credentials if not set, masking the password input (`read -s`).
* **Environment Variable & Secret Manager Support:** Supports `FLATEX_USER` and `FLATEX_PASSWORD` for automated runs or password manager integration (macOS Keychain, Linux `pass`, etc.).
* **Safe Parameter Encoding:** Automatically URL-encodes authentication payloads using `curl --data-urlencode` to support passwords containing special characters (`&`, `=`, `+`, etc.).
* **Secure Temporary Storage:** Replaced predictable `/tmp/cookies` with isolated temporary directories (`mktemp -d`) configured with restricted permissions (`0700`) and automatic cleanup traps on exit or interrupt.
* **No Remote Binary Downloads:** Removed automatic runtime downloading and executing of arbitrary binaries into `/tmp`. Dependencies are verified before execution.
* **Cross-Platform Compatibility:** Runs seamlessly on macOS (BSD tools) and Linux (GNU tools) without relying on GNU-specific `grep -P`.
* **Strict Error Handling:** Implements `set -euo pipefail` and validates session establishment.

---

## Requirements

* `curl`
* `jq`

Install them via your package manager if not already present:

* **macOS:** `brew install curl jq`
* **Debian / Ubuntu:** `sudo apt-get install curl jq`
* **Fedora / RHEL:** `sudo dnf install curl jq`

---

## Usage

### 1. Interactive Mode (Recommended for manual use)

Simply run the script. It will prompt for your username and password (the password will be masked):

```bash
./flatexporter.sh > my_portfolio.csv
```

### 2. Environment Variables (Automated jobs / scripts)

```bash
export FLATEX_USER="your_username"
export FLATEX_PASSWORD="your_password"
./flatexporter.sh > my_portfolio.csv
```

### 3. Password Manager Integration

#### macOS Keychain
Store your password in Keychain:
```bash
security add-generic-password -a "$USER" -s "flatex" -w "your_password"
```

Then run:
```bash
FLATEX_USER="your_username" FLATEX_PASSWORD=$(security find-generic-password -s "flatex" -w) ./flatexporter.sh > my_portfolio.csv
```

#### Linux `pass`
```bash
FLATEX_USER="your_username" FLATEX_PASSWORD=$(pass show banking/flatex) ./flatexporter.sh > my_portfolio.csv
```

---

## Two-Factor Authentication (2FA / PSD2) Note

Under European banking regulations (PSD2), flatex may require Strong Customer Authentication (SCA). If your account prompts for mobile app authorization, ensure you confirm any push notification or challenge in your flatex app when executing the script.

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE) (GPLv3).
Original author: [Andreas Hartmann](https://github.com/andreas-hartmann/flatexporter).
