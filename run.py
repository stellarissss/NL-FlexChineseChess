#!/usr/bin/env python3
"""无限制棋局 v2.0 启动器。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (ROOT, ROOT / "core", ROOT / "agent"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

import uvicorn
from main import app

if __name__ == "__main__":
    port = int(os.environ.get("CHESS_PORT", "9401"))
    host = os.environ.get("CHESS_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")
