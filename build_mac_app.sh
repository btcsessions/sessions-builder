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
# Create a simple Bitcoin-orange icon using macOS built-in tools
ICON_DIR="$SCRIPT_DIR/$APP_DIR/Contents/Resources/AppIcon.iconset"
mkdir -p "$ICON_DIR"

# Use Python to generate a PNG icon (no external deps needed)
python3 << 'PYICON'
import struct, zlib, os, sys

def create_btc_icon(size):
    """Create a Bitcoin-themed icon as raw RGBA pixels."""
    pixels = bytearray()
    cx, cy = size // 2, size // 2
    r = size // 2 - 1

    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = (dx*dx + dy*dy) ** 0.5

            if dist <= r:
                # Orange circle background
                R, G, B, A = 242, 169, 0, 255

                # Draw a simple "₿" shape
                # Scale everything relative to size
                s = size / 64.0

                # Vertical bars of B
                bar_x1 = int(22 * s)
                bar_x2 = int(26 * s)
                bar_top = int(16 * s)
                bar_bot = int(48 * s)

                # Top serif
                top_x1 = int(20 * s)
                top_x2 = int(28 * s)
                top_y1 = int(14 * s)
                top_y2 = int(18 * s)

                # Bottom serif
                bot_y1 = int(46 * s)
                bot_y2 = int(50 * s)

                # Upper bump of B
                bump1_cx = int(30 * s)
                bump1_cy = int(27 * s)
                bump1_r = int(9 * s)

                # Lower bump of B
                bump2_cx = int(31 * s)
                bump2_cy = int(39 * s)
                bump2_r = int(10 * s)

                # Horizontal bars
                hbar_y1_top = int(18 * s)
                hbar_y1_bot = int(21 * s)
                hbar_y2_top = int(32 * s)
                hbar_y2_bot = int(35 * s)
                hbar_y3_top = int(45 * s)
                hbar_y3_bot = int(48 * s)
                hbar_x1 = int(22 * s)
                hbar_x2 = int(36 * s)

                # Vertical strike-throughs
                strike_x1 = int(27 * s)
                strike_x2 = int(31 * s)
                strike_top = int(11 * s)
                strike_bot = int(53 * s)

                is_symbol = False

                # Vertical bar
                if bar_x1 <= x <= bar_x2 and bar_top <= y <= bar_bot:
                    is_symbol = True

                # Horizontal bars
                if hbar_x1 <= x <= hbar_x2:
                    if hbar_y1_top <= y <= hbar_y1_bot:
                        is_symbol = True
                    if hbar_y2_top <= y <= hbar_y2_bot:
                        is_symbol = True
                    if hbar_y3_top <= y <= hbar_y3_bot:
                        is_symbol = True

                # Upper bump
                bdx = x - bump1_cx
                bdy = y - bump1_cy
                if (bdx*bdx + bdy*bdy) <= bump1_r * bump1_r and x >= bar_x2:
                    # Ring (not filled)
                    inner_r = bump1_r - int(3 * s)
                    if (bdx*bdx + bdy*bdy) >= inner_r * inner_r:
                        is_symbol = True

                # Lower bump
                bdx = x - bump2_cx
                bdy = y - bump2_cy
                if (bdx*bdx + bdy*bdy) <= bump2_r * bump2_r and x >= bar_x2:
                    inner_r = bump2_r - int(3 * s)
                    if (bdx*bdx + bdy*bdy) >= inner_r * inner_r:
                        is_symbol = True

                # Strike-throughs
                if strike_x1 <= x <= strike_x2:
                    if strike_top <= y <= (strike_top + int(3*s)):
                        is_symbol = True
                    if (strike_bot - int(3*s)) <= y <= strike_bot:
                        is_symbol = True

                if is_symbol:
                    R, G, B = 255, 255, 255

                # Edge anti-aliasing
                if dist > r - 1.5:
                    alpha = max(0, min(255, int((r - dist + 1.5) * 170)))
                    A = alpha

            else:
                R, G, B, A = 0, 0, 0, 0

            pixels.extend([R, G, B, A])

    return bytes(pixels)


def write_png(filename, width, height, pixels):
    """Write RGBA pixels as PNG."""
    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter byte
        offset = y * width * 4
        raw.extend(pixels[offset:offset + width * 4])

    with open(filename, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(bytes(raw), 9)))
        f.write(chunk(b'IEND', b''))


# Generate all required icon sizes
iconset_dir = os.environ.get('ICON_DIR', '.')
sizes = [16, 32, 64, 128, 256, 512]

for sz in sizes:
    pixels = create_btc_icon(sz)
    write_png(os.path.join(iconset_dir, f'icon_{sz}x{sz}.png'), sz, sz, pixels)
    if sz <= 256:
        # @2x version
        big = sz * 2
        pixels2 = create_btc_icon(big)
        write_png(os.path.join(iconset_dir, f'icon_{sz}x{sz}@2x.png'), big, big, pixels2)

print("Icon PNGs generated.")
PYICON

# Rename to match macOS iconset naming convention
cd "$ICON_DIR"
mv icon_16x16.png icon_16x16.png 2>/dev/null || true
mv icon_16x16@2x.png icon_16x16@2x.png 2>/dev/null || true
mv icon_32x32.png icon_32x32.png 2>/dev/null || true
mv icon_32x32@2x.png icon_32x32@2x.png 2>/dev/null || true
mv icon_128x128.png icon_128x128.png 2>/dev/null || true
mv icon_128x128@2x.png icon_128x128@2x.png 2>/dev/null || true
mv icon_256x256.png icon_256x256.png 2>/dev/null || true
mv icon_256x256@2x.png icon_256x256@2x.png 2>/dev/null || true
mv icon_512x512.png icon_512x512.png 2>/dev/null || true
mv icon_64x64.png icon_32x32@2x.png 2>/dev/null || true

# Convert iconset to icns
if command -v iconutil &>/dev/null; then
    cd "$SCRIPT_DIR"
    iconutil -c icns "$ICON_DIR" -o "$SCRIPT_DIR/$APP_DIR/Contents/Resources/AppIcon.icns"
    rm -rf "$ICON_DIR"
    echo "✓ Icon converted to .icns"
else
    echo "⚠ iconutil not available. Icon will use PNG fallback."
fi

echo ""
echo "✓ Built: $SCRIPT_DIR/$APP_DIR"
echo ""
echo "You can now:"
echo "  • Double-click '$APP_NAME.app' in Finder to launch"
echo "  • Drag it to your Applications folder"
echo "  • Drag it to your Dock for quick access"
echo ""
