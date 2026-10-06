#!/bin/bash
# LUMI 위치 도우미(LUMILocator.app) 빌드 — Xcode 명령줄 도구(swiftc) 필요
# Apple Silicon/Intel 둘 다 돌도록 universal로 만들고 ad-hoc 서명한다.
set -e
cd "$(dirname "$0")"
TMP=$(mktemp -d)
APP=LUMILocator.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"
swiftc -O -target arm64-apple-macos12 main.swift -o "$TMP/arm64"
swiftc -O -target x86_64-apple-macos13 main.swift -o "$TMP/x86_64"
lipo -create "$TMP/arm64" "$TMP/x86_64" -output "$APP/Contents/MacOS/lumi-locator"
codesign --force -s - "$APP"
rm -rf "$TMP"
echo "빌드 완료: $(pwd)/$APP"
