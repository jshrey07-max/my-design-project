# my-design-project

## Microsoft Clarity MCP server

This project is configured (see `.mcp.json`) to run the [Microsoft Clarity MCP
server](https://www.npmjs.com/package/@microsoft/clarity-mcp-server), which
lets Claude query Clarity behavior-analytics data (heatmaps, session
recordings, insights) for this project.

To use it, copy `.env.example` to `.env` and set `CLARITY_API_TOKEN` to a
data-export token from Clarity (Settings > Data export). The token is read
from the environment — it is never committed to the repo.