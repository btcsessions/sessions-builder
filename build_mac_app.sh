#!/bin/bash
# Build a macOS .app bundle for BTC Sessions Planner
# Run this once on your Mac: bash build_mac_app.sh

set -e

APP_NAME="BTC Sessions Planner"
APP_DIR="$APP_NAME.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building $APP_NAME.app ..."

# Clean old bundle
rm -rf "$SCRIPT_DIR/$APP_DIR"

# Create bundle structure
mkdir -p "$SCRIPT_DIR/$APP_DIR/Contents/MacOS"
mkdir -p "$SCRIPT_DIR/$APP_DIR/Contents/Resources"

# --- Info.plist ---
cat > "$SCRIPT_DIR/$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>BTC Sessions Planner</string>
    <key>CFBundleDisplayName</key>
    <string>BTC Sessions Planner</string>
    <key>CFBundleIdentifier</key>
    <string>com.btcsessions.planner</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>launch</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSArchitecturePriority</key>
    <array>
        <string>arm64</string>
    </array>
</dict>
</plist>
PLIST

# --- Launch script ---
# Note: LAUNCHER is unquoted so $SCRIPT_DIR expands at build time (correct)
cat > "$SCRIPT_DIR/$APP_DIR/Contents/MacOS/launch" << LAUNCHER
#!/bin/bash

# Force arm64 to match arm64-compiled Python packages
# (macOS .app bundles sometimes default to x86_64)
if [ "\$(uname -m)" != "arm64" ]; then
    exec arch -arm64 /bin/bash "\$0" "\$@"
fi

# Finder-launched .app bundles get a minimal PATH — set it up
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:\$PATH"

# Tell Flask not to use reloader (keeps this process alive for Dock indicator)
export LAUNCHED_FROM_APP=1

# Project directory (baked in at build time)
PROJECT_DIR="$SCRIPT_DIR"
cd "\$PROJECT_DIR"

# Log to a file for debugging
LOG="\$PROJECT_DIR/launch.log"
exec > "\$LOG" 2>&1
echo "=== Launch at \$(date) ==="
echo "Arch: \$(uname -m)"
echo "PATH: \$PATH"

# Activate venv (use explicit python path as fallback)
PYTHON=python3
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    PYTHON="\$PROJECT_DIR/venv/bin/python"
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    PYTHON="\$PROJECT_DIR/.venv/bin/python"
fi
echo "Using python: \$PYTHON"
\$PYTHON --version

# Kill any existing instance on port 5000
/usr/sbin/lsof -ti:5000 | xargs kill -9 2>/dev/null
sleep 0.3

# Launch app
\$PYTHON app.py &
APP_PID=\$!
echo "Started app.py with PID \$APP_PID"

# Wait for server to be ready (up to 10 seconds)
READY=0
for i in {1..20}; do
    if /usr/bin/curl -s http://127.0.0.1:5000 >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.5
done

echo "Server ready: \$READY"
/usr/bin/open http://127.0.0.1:5000

# Keep the .app process alive as long as the server runs
wait \$APP_PID
echo "App exited with code \$?"
LAUNCHER
chmod +x "$SCRIPT_DIR/$APP_DIR/Contents/MacOS/launch"

# --- Generate icon ---
RESOURCES="$SCRIPT_DIR/$APP_DIR/Contents/Resources"
ICON_DIR="$RESOURCES/AppIcon.iconset"
mkdir -p "$ICON_DIR"
MASTER_PNG="$RESOURCES/_master_1024.png"

# Create a 1024x1024 master icon using Swift (guaranteed on macOS)
cat > /tmp/_btc_icon.swift << 'SWIFT'
import AppKit

let size = 1024
let outPath = CommandLine.arguments[1]

let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

// Orange circle
NSColor(calibratedRed: 0.949, green: 0.663, blue: 0.0, alpha: 1.0).setFill()
NSBezierPath(ovalIn: NSRect(x: 0, y: 0, width: size, height: size)).fill()

// White ₿ symbol
let font = NSFont.boldSystemFont(ofSize: 620)
let attrs: [NSAttributedString.Key: Any] = [
    .font: font,
    .foregroundColor: NSColor.white
]
let str = "₿" as NSString
let strSize = str.size(withAttributes: attrs)
let x = (CGFloat(size) - strSize.width) / 2
let y = (CGFloat(size) - strSize.height) / 2
str.draw(at: NSPoint(x: x, y: y), withAttributes: attrs)

image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else {
    exit(1)
}

try! png.write(to: URL(fileURLWithPath: outPath))
SWIFT

swiftc /tmp/_btc_icon.swift -o /tmp/_btc_icon -framework AppKit 2>/dev/null
/tmp/_btc_icon "$MASTER_PNG"
rm -f /tmp/_btc_icon /tmp/_btc_icon.swift

if [ ! -f "$MASTER_PNG" ]; then
    echo "⚠ Could not generate icon. Skipping."
else
    echo "✓ Master icon generated"

    # Use sips to create all required iconset sizes
    for sz in 16 32 128 256 512; do
        sips -z $sz $sz "$MASTER_PNG" --out "$ICON_DIR/icon_${sz}x${sz}.png" >/dev/null 2>&1
    done
    for sz in 16 32 128 256; do
        dbl=$((sz * 2))
        sips -z $dbl $dbl "$MASTER_PNG" --out "$ICON_DIR/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
    done
    cp "$MASTER_PNG" "$ICON_DIR/icon_512x512@2x.png"
    rm "$MASTER_PNG"

    # Convert iconset to icns
    iconutil -c icns "$ICON_DIR" -o "$RESOURCES/AppIcon.icns" && echo "✓ Icon converted to .icns"
    rm -rf "$ICON_DIR"
fi

echo ""
echo "✓ Built: $SCRIPT_DIR/$APP_DIR"
echo ""
echo "You can now:"
echo "  • Double-click '$APP_NAME.app' in Finder to launch"
echo "  • Drag it to your Applications folder"
echo "  • Drag it to your Dock for quick access"
echo ""
