#!/usr/bin/env bash
set -euo pipefail

APP_NAME="myusic"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
SOURCE_FILE="${SCRIPT_DIR}/mymusic_player.py"
DEST_DIR="${HOME}/.local/bin"
DEST_FILE="${DEST_DIR}/${APP_NAME}"

if [[ ! -f "${SOURCE_FILE}" ]]; then
    echo "Error: ${SOURCE_FILE} not found." >&2
    echo "Please run this installer from the myusic repository root." >&2
    exit 1
fi

echo "Installing ${APP_NAME} to ${DEST_FILE}..."
mkdir -p "${DEST_DIR}"
cp "${SOURCE_FILE}" "${DEST_FILE}"
chmod +x "${DEST_FILE}"

echo "Success: ${APP_NAME} has been installed."

case ":${PATH}:" in
    *":${DEST_DIR}:"*)
        echo "${DEST_DIR} is already in your PATH."
        echo "You can now run: myusic"
        ;;
    *)
        echo "Warning: ${DEST_DIR} is NOT in your PATH."
        echo "Add this line to your shell startup file (~/.bashrc, ~/.zshrc, ~/.profile, or equivalent):"
        echo
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo
        echo "Then reload your shell, for example:"
        echo
        echo "    source ~/.bashrc   # or ~/.zshrc / ~/.profile"
        echo
        echo "Until PATH is updated, run:"
        echo
        echo "    ${DEST_FILE}"
        ;;
esac

echo
echo "Recommended dependency (Arch Linux): sudo pacman -S mpv"
