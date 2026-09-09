"""HTTP surface of the `quant-mcp` service.

This is the transport the Go API calls over HTTP/JSON (planning.md §4
decision 1). It is a thin adapter: every endpoint delegates to the
framework-agnostic quant engine, so the same capability is reachable from a
unit test, from the MCP layer, and from the API without divergence.
"""

from services.quant.webapi.app import create_app

__all__ = ["create_app"]
