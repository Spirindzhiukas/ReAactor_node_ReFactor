#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
echo "[ReFactor] Updating ReActor ReFactor from the repository..."
git pull --ff-only
echo
echo "[ReFactor] If dependencies changed, re-run: python install.py --yes"
