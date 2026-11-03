"""Absolute-path MCP entrypoint; works regardless of client working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ips3608.mcp_server import main

if __name__ == "__main__":
    main()
