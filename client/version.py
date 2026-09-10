import json
import sys
from pathlib import Path


manifest = (Path(__file__).with_name("release.json") if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1] / "server" / "app" / "release.json")
APP_VERSION = json.loads(manifest.read_text(encoding="utf-8"))["version"]
