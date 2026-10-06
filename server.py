#!/usr/bin/env python3
"""Locus panel-only MCP entrypoint. Run with Locus's bundled mcp==2.0.0."""
import asyncio
import json
import os
import threading

from openpost_plugin.core import Studio, StudioError


def create_server(studio=None):
    from mcp.server.mcpserver import Context, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("Social Studio")
    if studio is None:
        root = os.environ.get("LOCUS_PLUGIN_DATA")
        if not root:
            raise RuntimeError("LOCUS_PLUGIN_DATA must be provided by the Locus plugin installer.")
        studio = Studio(root)

    @server.tool(name="social_studio", structured_output=False, description="Project-scoped Social Studio panel operations. Requires native com.locus/panel request metadata; unavailable to chat agents.")
    async def social_studio(operation: str, payload: dict, ctx: Context) -> str:
        metadata = ctx.request_context.meta
        metadata = metadata.model_dump(by_alias=True, exclude_none=True) if hasattr(metadata, "model_dump") else dict(metadata or {})
        cancelled = threading.Event()

        async def observe_cancellation():
            await ctx.request_context.cancel_requested.wait()
            cancelled.set()

        watcher = asyncio.create_task(observe_cancellation())
        try:
            result = await asyncio.to_thread(studio.call, operation, payload, metadata, cancelled)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except StudioError as error:
            raise ToolError(str(error)) from None
        except Exception:
            # Never expose native/transport exception text, arguments, or a token.
            raise ToolError("Social Studio couldn't complete this request. Reopen the panel and try again; refresh Activity before retrying a publication action.") from None
        finally:
            watcher.cancel()

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio")
