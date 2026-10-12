#!/usr/bin/env bash
# Double-click this file in Finder, or run: ./launch_mac.command
# Written for the bash 3.2 that ships with macOS.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="${SCRIPT_DIR}/BoundingBoxNavigator"

pause_on_error() {
    echo
    read -rp "Press Enter to close... " _
}

if [ ! -f "${MODULE_DIR}/BoundingBoxNavigator.py" ]; then
    echo "[ERROR] Module not found at: ${MODULE_DIR}/BoundingBoxNavigator.py"
    echo
    echo "If you opened this from inside a .zip, extract the archive first"
    echo "(double-click the zip, or: unzip the file), then run this launcher"
    echo "from the extracted folder."
    pause_on_error
    exit 1
fi

# Print the Slicer executable inside a .app bundle, or nothing if it is not one.
slicer_bin_in_app() {
    app="$1"
    bin="${app%/}/Contents/MacOS/Slicer"
    if [ -x "$bin" ]; then
        printf '%s\n' "$bin"
    fi
}

app_version() {
    app="$1"
    plist="${app%/}/Contents/Info.plist"
    version=""
    if [ -f "$plist" ] && [ -x /usr/libexec/PlistBuddy ]; then
        version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist" 2>/dev/null || true)
    fi
    if [ -z "$version" ]; then
        base="$(basename "$app" .app)"
        case "$base" in
            Slicer-*)
                version="${base#Slicer-}"
                version="${version%%-*}"
                ;;
        esac
    fi
    printf '%s\n' "$version"
}

# Return 0 when dotted version $1 is strictly newer than $2.
version_gt() {
    a="$1"
    b="$2"
    while [ -n "$a" ] || [ -n "$b" ]; do
        x="${a%%.*}"
        y="${b%%.*}"
        if [ "$a" = "$x" ]; then
            a=""
        else
            a="${a#*.}"
        fi
        if [ "$b" = "$y" ]; then
            b=""
        else
            b="${b#*.}"
        fi
        x="${x%%[!0-9]*}"
        y="${y%%[!0-9]*}"
        x="${x:-0}"
        y="${y:-0}"
        if [ "$x" -gt "$y" ]; then
            return 0
        fi
        if [ "$x" -lt "$y" ]; then
            return 1
        fi
    done
    return 1
}

# Newline-separated lists. A leading newline keeps line numbers aligned.
FOUND_BINS=""
FOUND_VERSIONS=""

remember_bin() {
    bin="$1"
    [ -n "$bin" ] && [ -x "$bin" ] || return 0
    case "
${FOUND_BINS}
" in
        *"
${bin}
"*) return 0 ;;
    esac
    app="$(cd "$(dirname "$bin")/../.." && pwd)"
    version="$(app_version "$app")"
    FOUND_BINS="${FOUND_BINS}
${bin}"
    FOUND_VERSIONS="${FOUND_VERSIONS}
${version}"
}

search_app_globs() {
    for app in "$@"; do
        [ -d "$app" ] || continue
        remember_bin "$(slicer_bin_in_app "$app")"
    done
}

if command -v Slicer >/dev/null 2>&1; then
    path_bin="$(command -v Slicer)"
    if [ -x "$path_bin" ]; then
        remember_bin "$path_bin"
    fi
fi

search_app_globs /Applications/Slicer.app /Applications/Slicer-*.app \
    "$HOME/Applications/Slicer.app" "$HOME/Applications/Slicer-*.app"

if command -v mdfind >/dev/null 2>&1; then
    while IFS= read -r app; do
        [ -n "$app" ] || continue
        remember_bin "$(slicer_bin_in_app "$app")"
    done <<EOF
$(mdfind "kMDItemCFBundleIdentifier == 'org.slicer.slicer'" 2>/dev/null || true)
EOF
fi

app_of_bin() {
    bin="$1"
    case "$bin" in
        */Contents/MacOS/Slicer)
            dirname "$(dirname "$(dirname "$bin")")"
            ;;
        *)
            dirname "$bin"
            ;;
    esac
}

# A copy in /Applications or ~/Applications. A disk image or Downloads copy is not installed.
in_applications() {
    case "$1" in
        /Applications/*|"$HOME/Applications"/*)
            return 0
            ;;
    esac
    return 1
}

any_installed_slicer() {
    while IFS= read -r bin || [ -n "$bin" ]; do
        [ -n "$bin" ] || continue
        if in_applications "$(app_of_bin "$bin")"; then
            return 0
        fi
    done <<EOF
$FOUND_BINS
EOF
    return 1
}

# Newest installed Slicer. A copy in Applications wins over one on a disk image
# or in Downloads, even if that other copy looks newer.
# The loop stays in this shell (no pipe) so best/best_version are visible afterwards.
pick_best_bin() {
    best=""
    best_version=""
    only_installed=0
    if any_installed_slicer; then
        only_installed=1
    fi
    i=0
    while IFS= read -r bin || [ -n "$bin" ]; do
        i=$((i + 1))
        ver=$(printf '%s\n' "$FOUND_VERSIONS" | sed -n "${i}p")
        [ -n "$bin" ] || continue
        if [ "$only_installed" -eq 1 ]; then
            in_applications "$(app_of_bin "$bin")" || continue
        fi
        if [ -z "$best" ]; then
            best="$bin"
            best_version="$ver"
            continue
        fi
        if [ -n "$ver" ] && { [ -z "$best_version" ] || version_gt "$ver" "$best_version"; }; then
            best="$bin"
            best_version="$ver"
        fi
    done <<EOF
$FOUND_BINS
EOF
    printf '%s\n' "$best"
}

# Desktop, Documents, and Downloads are protected. Slicer cannot re-read a module
# stored there after a restart, so the module disappears. Disk images are the same.
path_is_blocked_for_slicer() {
    path="$1"
    case "$path" in
        /Volumes/*|/private/var/folders/*|"$HOME/Library/Mobile Documents"/*)
            return 0
            ;;
    esac
    for protected in "$HOME/Desktop" "$HOME/Documents" "$HOME/Downloads" \
        "$HOME/Movies" "$HOME/Pictures" "$HOME/Music"; do
        case "$path" in
            "$protected"|"$protected"/*)
                return 0
                ;;
        esac
    done
    return 1
}

resolve_user_path() {
    raw="$1"
    raw="${raw#\"}"
    raw="${raw%\"}"
    raw="${raw#\'}"
    raw="${raw%\'}"
    # Finder drag-and-drop escapes spaces as '\ '.
    raw="${raw//\\ / }"
    raw="${raw%/}"
    if [ -x "$raw" ] && [ ! -d "$raw" ]; then
        printf '%s\n' "$raw"
        return 0
    fi
    if [ -d "$raw" ]; then
        case "$raw" in
            *.app)
                bin="$(slicer_bin_in_app "$raw")"
                if [ -n "$bin" ]; then
                    printf '%s\n' "$bin"
                    return 0
                fi
                ;;
        esac
        found=$(find "$raw" -maxdepth 4 -type f -path '*/Contents/MacOS/Slicer' 2>/dev/null | head -n 1 || true)
        if [ -n "$found" ] && [ -x "$found" ]; then
            printf '%s\n' "$found"
            return 0
        fi
    fi
    return 1
}

SLICER_BIN="$(pick_best_bin)"

if [ -z "$SLICER_BIN" ] || [ ! -x "$SLICER_BIN" ]; then
    echo "Could not find 3D Slicer in /Applications or ~/Applications."
    echo
    echo "Install it like this, then run this launcher again:"
    echo "  1. Open the Slicer disk image."
    echo "  2. Drag Slicer into Applications. Do not run it from the disk image."
    echo "  3. In Applications, right-click Slicer, choose Open, then click Open."
    echo "     macOS blocks apps from outside the App Store until you do this once."
    echo
    echo "If Slicer is already in Applications, drag Slicer.app into this window"
    echo "and press Enter."
    echo
    read -rp "Path to Slicer (or Enter to quit): " USER_INPUT
    if [ -z "$USER_INPUT" ]; then
        exit 0
    fi
    if ! SLICER_BIN="$(resolve_user_path "$USER_INPUT")"; then
        echo "[ERROR] Could not find a Slicer executable from: $USER_INPUT"
        pause_on_error
        exit 1
    fi
fi

if [ ! -x "$SLICER_BIN" ]; then
    echo "[ERROR] Executable not found at: $SLICER_BIN"
    pause_on_error
    exit 1
fi

SLICER_APP="$(app_of_bin "$SLICER_BIN")"
setup_problem=0

if path_is_blocked_for_slicer "$MODULE_DIR"; then
    echo "[ERROR] The BoundingBoxNavigator folder is in a location macOS protects:"
    echo "  $MODULE_DIR"
    echo
    echo "Slicer cannot keep this module after a restart from Downloads, Desktop,"
    echo "Documents, or a disk image. The module then disappears."
    echo
    echo "Move the extracted tool folder into your home folder, then run this again:"
    echo "  1. In Finder, press Cmd+Shift+H. That opens your home folder."
    echo "  2. Move the whole tool folder there. Not into Downloads, Desktop, or Documents."
    echo "  3. Double-click launch_mac.command from that new place."
    echo
    setup_problem=1
fi

if ! in_applications "$SLICER_APP"; then
    echo "[ERROR] 3D Slicer is not installed in Applications:"
    echo "  $SLICER_APP"
    echo
    echo "Running it from the disk image or from Downloads does not stick. macOS"
    echo "blocks the app, and the module is gone the next time Slicer starts."
    echo
    echo "  1. Open the Slicer disk image."
    echo "  2. Drag Slicer into Applications."
    echo "  3. In Applications, right-click Slicer, choose Open, then click Open."
    echo "  4. Run this launcher again."
    echo
    setup_problem=1
fi

if [ "$setup_problem" -ne 0 ]; then
    pause_on_error
    exit 1
fi

if command -v xattr >/dev/null 2>&1 && xattr -p com.apple.quarantine "$SLICER_APP" >/dev/null 2>&1; then
    echo "If macOS says Slicer cannot be opened, go to Applications, right-click"
    echo "Slicer, choose Open, then click Open. That approval is only needed once."
    echo
fi

echo "[OK] Using Slicer: $SLICER_BIN"
echo "[OK] Module path:  $MODULE_DIR"
echo "Launching 3D Slicer..."

exec "$SLICER_BIN" --additional-module-paths "$MODULE_DIR" --python-code "slicer.util.selectModule('BoundingBoxNavigator')" "$@"
