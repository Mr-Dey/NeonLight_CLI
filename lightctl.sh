#!/bin/sh
# Linux / macOS launcher (link it as ~/.local/bin/lightctl): runs lightctl.py with the virtual environment next to this script,
# wherever it is called from (follows symlinks, e.g. ~/.local/bin/lightctl -> this file).
here=$(dirname "$(readlink -f "$0")")
exec "$here/.venv/bin/python" "$here/lightctl.py" "$@"
