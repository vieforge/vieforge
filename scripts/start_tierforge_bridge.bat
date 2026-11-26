@echo off
title TierForge AI Bridge Daemon (127.0.0.1:19876)
cd /d "D:\tierforge-harness"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\extension_bridge_server.py" serve
) else (
  python "scripts\extension_bridge_server.py" serve
)
