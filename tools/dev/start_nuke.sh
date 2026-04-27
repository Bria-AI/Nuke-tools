#!/bin/bash

# Resolve repo root dynamically from this script path
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"

export BRIA_EXTENSION_ROOT="$REPO_ROOT"
export NUKE_BRIA="$REPO_ROOT"
export BRIA_NUKE_STARTUP_DIR="$REPO_ROOT/nuke"
export BRIA_NUKE_ROOT="$BRIA_NUKE_STARTUP_DIR"
export BRIA_DEBUG=1

if [[ -z "$BRIA_API_KEY_NUKE" && -n "$BRIA_API_KEY_HOUDINI" ]]; then
	export BRIA_API_KEY_NUKE="$BRIA_API_KEY_HOUDINI"
fi

if [[ -n "$NUKE_PATH" ]]; then
	export NUKE_PATH="$BRIA_NUKE_STARTUP_DIR:$NUKE_PATH"
else
	export NUKE_PATH="$BRIA_NUKE_STARTUP_DIR"
fi

# TLS/SSL certificate bundle for Nuke's embedded Python
BRIA_CA_BUNDLE_PATH=""
for candidate in \
	"/etc/ssl/cert.pem" \
	"/private/etc/ssl/cert.pem" \
	"/opt/homebrew/etc/ca-certificates/cert.pem" \
	"/etc/ssl/certs/ca-certificates.crt" \
	"/etc/pki/tls/certs/ca-bundle.crt" \
	"/etc/ssl/ca-bundle.pem"; do
	if [[ -f "$candidate" ]]; then
		BRIA_CA_BUNDLE_PATH="$candidate"
		break
	fi
done

if [[ -n "$BRIA_CA_BUNDLE_PATH" ]]; then
	export BRIA_CA_BUNDLE="$BRIA_CA_BUNDLE_PATH"
	export SSL_CERT_FILE="$BRIA_CA_BUNDLE_PATH"
	export REQUESTS_CA_BUNDLE="$BRIA_CA_BUNDLE_PATH"
fi

# Disable Nuke FrameServer by default in dev runs (override by pre-setting this env var)
export NUKE_DISABLE_FRAMESERVER="${NUKE_DISABLE_FRAMESERVER:-1}"

# Launch Nuke — update paths below for your local installation.
OS="$(uname -s)"
case "$OS" in
	Darwin)
		# macOS — update this path to your Nuke version
		open -a "/Applications/Nuke17.0v1/Nuke17.0v1 Non-Commercial.app"
		;;
	Linux)
		# Linux — update this path to your Nuke install location
		NUKE_BIN="/usr/local/Nuke17.0v1/Nuke17.0"
		if [[ ! -x "$NUKE_BIN" ]]; then
			# Try common alternative locations
			for try_bin in \
				"/opt/Nuke17.0v1/Nuke17.0" \
				"$(which Nuke17.0 2>/dev/null)" \
				"$(which nuke 2>/dev/null)"; do
				if [[ -x "$try_bin" ]]; then
					NUKE_BIN="$try_bin"
					break
				fi
			done
		fi
		if [[ -x "$NUKE_BIN" ]]; then
			exec "$NUKE_BIN" "$@"
		else
			echo "[Bria] Could not find Nuke binary. Edit NUKE_BIN in this script."
			exit 1
		fi
		;;
	*)
		echo "[Bria] Unsupported platform: $OS"
		exit 1
		;;
esac
