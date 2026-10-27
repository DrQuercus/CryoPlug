"""CryoPlug for AI agents: a local assistant (Claude Code, Claude Desktop, Codex, LM Studio, Goose, Open WebUI...)
drives a running CryoPlug server through the Model Context Protocol, guided by agent skills.

* :mod:`cryoplug.agent.client` — HTTP client of the server (where to connect, which credential);
* :mod:`cryoplug.agent.tools` — the tools an agent calls (projects, job types, jobs, reports, workflows, files),
  answering compact text written for language models, small local ones included;
* :mod:`cryoplug.agent.mcp` — the MCP server: ``cryoplug mcp`` over stdio, and ``/mcp`` on the CryoPlug server
  itself (Streamable HTTP) for clients that connect to a URL;
* :mod:`cryoplug.agent.openapi` — the same tools as an OpenAPI tool server, ``/openapi`` on the CryoPlug server, for
  chat assistants that call tools described by OpenAPI (Open WebUI's tool servers), with the descriptions in English
  or in French (:mod:`cryoplug.agent.tools_fr`);
* :mod:`cryoplug.agent.skills` — the agent skills (``SKILL.md`` folders in ``cryoplug/skills``) that teach an agent
  the cryo-EM work done with CryoPlug, installable with ``cryoplug ai skills --install``.

The agent works with the rights of its credential: an API token of a user account (Settings › AI agents), or the
access token / password of a server without accounts.  It cannot delete projects or jobs, manage accounts or run
arbitrary commands (``custom_command``, extra program arguments) unless ``cryoplug mcp --allow-commands``.
"""
