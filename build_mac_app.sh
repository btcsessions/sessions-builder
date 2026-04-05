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
</dict>
</plist>
PLIST

# --- Launch script ---
cat > "$SCRIPT_DIR/$APP_DIR/Contents/MacOS/launch" << LAUNCHER
#!/bin/bash
cd "$SCRIPT_DIR"

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Kill any existing instance
pkill -f "python app.py" 2>/dev/null
sleep 0.3

# Launch app
python app.py &
APP_PID=\$!

# Wait for server
for i in {1..15}; do
    if curl -s http://localhost:5000 >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Open browser
open http://localhost:5000

# Keep running
wait \$APP_PID
LAUNCHER
chmod +x "$SCRIPT_DIR/$APP_DIR/Contents/MacOS/launch"

# --- Generate icon ---
# Use Automator/JS to create a Bitcoin-orange circle icon via macOS graphics
RESOURCES="$SCRIPT_DIR/$APP_DIR/Contents/Resources"
ICON_DIR="$RESOURCES/AppIcon.iconset"
mkdir -p "$ICON_DIR"
MASTER_PNG="$RESOURCES/_master_1024.png"

# Create a 1024x1024 master icon using macOS built-in JavaScript for Automation
osascript -l JavaScript << 'JSICON' - "$MASTER_PNG"
ObjC.import('AppKit');
ObjC.import('Foundation');

var outPath = $.NSProcessInfo.processInfo.arguments.objectAtIndex(4).js;
var size = 1024;

var rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaPlanarColorSpaceNameBytesPerRowBitsPerPixel(
    null, size, size, 8, 4, true, false,
    $.NSDeviceRGBColorSpace, size * 4, 32
);

var ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep);
$.NSGraphicsContext.setCurrentContext(ctx);

// Orange circle
var orange = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.949, 0.663, 0.0, 1.0);
orange.set;
var path = $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(0, 0, size, size));
path.fill;

// White Bitcoin "B" symbol using text rendering
var white = $.NSColor.whiteColor;
var font = $.NSFont.fontWithNameSize("Helvetica-Bold", 620);
var attrs = $.NSMutableDictionary.alloc.init;
attrs.setObjectForKey(font, $.NSFontAttributeName);
attrs.setObjectForKey(white, $.NSForegroundColorAttributeName);

var str = $.NSString.alloc.initWithUTF8String("₿");
var strSize = str.sizeWithAttributes(attrs);
var x = (size - strSize.width) / 2;
var y = (size - strSize.height) / 2 - 20;
str.drawAtPointWithAttributes($.NSMakePoint(x, y), attrs);

ctx.flushGraphics;

var data = rep.representationUsingTypeProperties($.NSPNGFileType, null);
data.writeToFileAtomically(outPath, true);
JSICON

if [ ! -f "$MASTER_PNG" ]; then
    echo "⚠ Could not generate icon via osascript. Skipping icon."
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
