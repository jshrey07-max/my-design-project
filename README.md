# my-design-project

## Microsoft Clarity MCP server

This project is configured to use the [Microsoft Clarity MCP server](https://github.com/microsoft/clarity-mcp-server) (see `.mcp.json`), which lets Claude query Clarity analytics data and session recordings.

To use it:

1. Get a Clarity Data Export API token: in your Clarity project, go to **Settings → Data Export → Generate new API token**.
2. Set it as an environment variable before starting Claude Code:
   ```bash
   export CLARITY_API_TOKEN=your-token-here
   ```
3. Restart Claude Code so it picks up the server from `.mcp.json`.
