#!/usr/bin/env bash
# Launch the Qt GUI from anywhere with the project as working directory.
cd "$(dirname "$0")" || exit 1
exec python3 fileflow_qt.py "$@"
