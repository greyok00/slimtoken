"""Run the slimtoken MCP server via python -m slimtoken.mcp_server."""
import sys
from .server import main
if __name__ == '__main__':
    sys.exit(main())
