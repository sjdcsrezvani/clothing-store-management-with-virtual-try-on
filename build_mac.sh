#!/usr/bin/env bash
# Build the Raykid Store desktop app for macOS.
# Produces dist/Raykid Store.app
#
# Prerequisites:
#   - Python 3.10+ (this project uses 3.12)
#   - All dependencies from requirements.txt installed
#   - pywebview + pyinstaller installed
#
# Usage:
#   ./build_mac.sh
#
# Note: the resulting .app is unsigned. To distribute, you'll need to
# codesign + notarize it with your Apple Developer ID. For local shop
# installs, the unsigned .app works if the user right-clicks → Open on
# first launch (to bypass Gatekeeper).

set -euo pipefail

cd "$(dirname "$0")"

echo "📦 Building Raykid Store desktop app for macOS..."

# Ensure build deps are installed
python3 -c "import PyInstaller" 2>/dev/null || {
    echo "Installing PyInstaller..."
    pip install pyinstaller
}
python3 -c "import webview" 2>/dev/null || {
    echo "Installing pywebview..."
    pip install pywebview
}

# Clean previous builds
rm -rf build/ dist/ *.spec.bak

# Run PyInstaller with our spec
python3 -m PyInstaller raykid_store.spec --noconfirm --clean

echo ""
echo "✅ Build complete!"
echo "   App: dist/Raykid Store.app"
echo ""
echo "   To run: open 'dist/Raykid Store.app'"
echo "   (On first launch, right-click → Open to bypass Gatekeeper)"
echo ""
echo "   The app stores its data in:"
echo "   ~/Library/Application Support/RaykidStore/"
