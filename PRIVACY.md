# Privacy Policy

Last updated: 2026-11-16

FusionKit (the `fusion_kit` library and the Claude Code plugin in this repository) does not collect, store or transmit personal data. It contains no telemetry and no analytics.

## Local processing

- The hooks are local processes on your machine. They make no network requests.
- Fusion scripts run inside your own copy of Autodesk Fusion. Claude Code talks to Fusion through the Fusion MCP server at `http://127.0.0.1:27182/mcp`, which is reachable only on localhost.
- The optional fusion-cad MCP server (`https://github.com/Mfrostbutter/fusion-cad-mcp`) runs as a local process when you install and enable it. It is not part of this repository. Read its documentation for its data handling.
- FusionKit does not send design data, model files or usage data to its author or to any third party.

## Claude Code

Conversation content and tool calls are handled by Claude Code and by the model service you use, under their own terms. This plugin does not change that.

## Contact

Questions about this policy: open an issue at `https://github.com/albertlis/FusionKit/issues`.
