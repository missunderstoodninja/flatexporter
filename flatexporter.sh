#!/usr/bin/env bash

#######################################
#  .d888888        888                                          888
# d88P" 888        888                                          888
# 888   888        888                                          888
# 888888888 8888b. 888888 .d88b. 888  88888888b.  .d88b. 888d888888888 .d88b. 888d888
# 888   888    "88b888   d8P  Y8b`Y8bd8P'888 "88bd88""88b888P"  888   d8P  Y8b888P"
# 888   888.d888888888   88888888  X88K  888  888888  888888    888   88888888888
# 888   888888  888Y88b. Y8b.    .d8""8b.888 d88PY88..88P888    Y88b. Y8b.    888
# 888   888"Y888888 "Y888 "Y8888 888  88888888P"  "Y88P" 888     "Y888 "Y8888 888
#                                        888
#                                        888
#                                        888
#
# Exports the contents of a flatex depot as csv.
#
# Requires: curl, jq
#
# Usage:
#   Interactive prompt:   ./flatexporter.sh
#   Environment vars:     FLATEX_USER=... FLATEX_PASSWORD=... ./flatexporter.sh
#   macOS Keychain:       FLATEX_PASSWORD=$(security find-generic-password -s "flatex" -w) ./flatexporter.sh
#
# Author: Andreas Hartmann (https://github.com/andreas-hartmann/flatexporter)
# Security & portability improvements: https://github.com/missunderstoodninja/flatexporter
#
# License: GNU General Public License v3.0 (GPLv3)
#######################################

set -euo pipefail

# Check required dependencies
check_dependencies() {
	local missing=()
	for cmd in curl jq; do
		if ! command -v "$cmd" >/dev/null 2>&1; then
			missing+=("$cmd")
		fi
	done

	if [ ${#missing[@]} -gt 0 ]; then
		echo "Error: Missing required dependencies: ${missing[*]}" >&2
		echo "Please install them via your package manager:" >&2
		echo "  macOS:         brew install ${missing[*]}" >&2
		echo "  Debian/Ubuntu: sudo apt-get install ${missing[*]}" >&2
		echo "  Fedora/RHEL:   sudo dnf install ${missing[*]}" >&2
		exit 1
	fi
}

# Prompt or load credentials securely without exposing them in process lists or shell history
get_credentials() {
	USERNAME="${FLATEX_USER:-}"
	PASSWORD="${FLATEX_PASSWORD:-}"

	if [ -z "$USERNAME" ]; then
		read -r -p "Flatex Username / Account Number: " USERNAME
	fi

	if [ -z "$PASSWORD" ]; then
		read -r -s -p "Flatex Password: " PASSWORD
		echo "" >&2
	fi

	if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
		echo "Error: Both username and password are required." >&2
		exit 1
	fi
}

# Set up an isolated temporary workspace with strict permissions (0700) and auto-cleanup
setup_temp_dir() {
	umask 077
	WORKDIR="$(mktemp -d -t flatexporter.XXXXXX)"
	COOKIE_JAR="${WORKDIR}/cookies.txt"

	# Ensure temp directory is always deleted on exit or signal
	trap 'rm -rf "${WORKDIR}"' EXIT INT TERM HUP
}

main() {
	check_dependencies
	get_credentials
	setup_temp_dir

	>&2 echo "Authenticating with flatex.de..."

	# Log in and store cookies in our isolated cookie jar.
	# Using --data-urlencode ensures special characters in passwords do not corrupt the request.
	curl -sL --post302 \
		--cookie-jar "$COOKIE_JAR" \
		--cookie "$COOKIE_JAR" \
		--compressed \
		--data-urlencode "tx_flatexaccounts_singlesignonbanking[uname_app]=${USERNAME}" \
		--data-urlencode "tx_flatexaccounts_singlesignonbanking[password_app]=${PASSWORD}" \
		--output /dev/null \
		'https://www.flatex.de/sso'

	# Extract JSESSIONID portably (works across Linux, macOS, BSD without GNU grep -P)
	jsessionid=$(awk '$0 ~ /JSESSIONID/ {print $NF}' "$COOKIE_JAR" | tail -n 1)

	if [ -z "$jsessionid" ]; then
		>&2 echo "Error: Authentication failed or 2FA confirmation required."
		>&2 echo "Please verify credentials and check if a 2FA challenge is pending in your flatex app."
		exit 1
	fi

	>&2 echo "Requesting CSV download link..."

	# Ask for the CSV file download link and extract it.
	# X-tokenId needs to be set to any value, X-AJAX must be true.
	ajax_response=$(curl -s 'https://konto.flatex.de/banking-flatex/ajaxCommandServlet' \
		--cookie-jar "$COOKIE_JAR" \
		--cookie "$COOKIE_JAR" \
		-H 'X-tokenId: 0' \
		-H 'X-AJAX: true' \
		-H "Cookie: JSESSIONID=$jsessionid" \
		--data-raw "command=triggerAction&widgetId=depositStatementForm_tableActionCombobox_entriesI1I&widgetName=tableActionCombobox.entries%5B1%5D&eventType=click&delay=0" \
		--compressed)

	script_content=$(echo "$ajax_response" | jq -r '.commands[0].script // empty')

	if [ -z "$script_content" ]; then
		>&2 echo "Error: Did not receive expected response from flatex. The session may have expired."
		exit 1
	fi

	# Extract the CSV download path using portable sed
	csvpath=$(echo "$script_content" | sed -n 's/.*DownloadDocumentBrowserBehaviorsClick\.finished("\([^"]*\.csv\)".*/\1/p')

	if [ -z "$csvpath" ]; then
		>&2 echo "Error: Could not find CSV download path in flatex response."
		exit 1
	fi

	>&2 echo "Downloading CSV..."

	# Download and stream the CSV to standard output
	curl -s "https://konto.flatex.de$csvpath" \
		--cookie-jar "$COOKIE_JAR" \
		--cookie "$COOKIE_JAR" \
		-H "Cookie: JSESSIONID=$jsessionid" \
		--compressed
}

main "$@"
