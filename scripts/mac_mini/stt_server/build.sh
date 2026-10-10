#!/bin/bash
# Build mitra-stt.app (Apple on-device speech -> HTTP for the Pi5). Needs only
# the Xcode Command Line Tools.
#
# Why an .app and not a bare binary: macOS checks the Speech Recognition
# permission against the RESPONSIBLE process. Run from a terminal, that is the
# terminal, which has no NSSpeechRecognitionUsageDescription -> SIGABRT
# (TCC_CRASHING_DUE_TO_PRIVACY_VIOLATION, seen 2026-10-10). Started with
# `open` or by launchd, the bundle is its own responsible process and gets
# its own entry in System Settings > Privacy & Security > Speech Recognition.
set -euo pipefail
cd "$(dirname "$0")"
APP=mitra-stt.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"
swiftc -O -swift-version 5 main.swift -o "$APP/Contents/MacOS/mitra-stt"
codesign -s - -f "$APP"
echo "built $(pwd)/$APP"
echo "first run (asks for permission once): open $(pwd)/$APP"
