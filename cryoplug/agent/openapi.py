"""OpenAPI tool server: the agent tools for chat assistants that call tools described by an OpenAPI specification —
the "tool servers" of Open WebUI, and other OpenAPI tool clients.

* ``GET /openapi/openapi.json``: the specification, one operation per tool (``POST /openapi/<tool>``, operationId
  ``cryoplug_<tool>``: explicit names that do not collide with the tools of other servers), the arguments as a JSON
  body.  Public, as documentation is: it holds no data, and Open WebUI reads it without credentials when it forwards
  each person's own session to the tools (its "session" authentication, with ``[external_login]``).
* ``POST /openapi/<tool>``: runs the tool with the credential of the request, the access control of the server
  applying first as to any request (an API token of a CryoPlug account as Bearer key, or the person's session of
  the other application with ``[external_login]``).  The answer is the tool's compact text (``text/plain``), cut at
  ``max_answer_chars`` (local models have small contexts); images come as data URIs in a JSON answer (Open WebUI
  shows them in the conversation); errors are JSON ``{"error": ...}`` saying what to correct, with an error status.

``[openapi]`` in the configuration chooses the tools (``read`` by default: an assistant that looks and changes
nothing; writing is protected by this choice and by the rights of the credential, never by instructions to the
model) and the language of the descriptions (``fr``: written for assistants working in French, see
:mod:`cryoplug.agent.tools_fr`).  With ``[server] public_url``, the answers about a project or a job end with the
link to its page.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Iterable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

from cryoplug.agent import tools as toolmod
from cryoplug.agent.http import AsgiClient
from cryoplug.agent.tools_fr import FR
from cryoplug.config import Config

BASE = "/openapi"
SPEC_PATH = f"{BASE}/openapi.json"
PREFIX = "cryoplug_"
log = logging.getLogger("cryoplug.openapi")

TEXTS = {
    "en": {
        "info": "CryoPlug, the cryo-EM processing interface of this server (RELION, CryoSPARC imports, model building, "
                "refinement, validation): its projects, jobs, results and logs, with the rights of the account used.",
        "readonly": " Read only: creating, queueing or stopping jobs is done in the CryoPlug web interface{where}.",
        "ok": "The tool's answer: compact text for the model (with images, JSON with data URIs).",
        "error": "What to correct, or why CryoPlug refused.",
        "cut": "[… answer cut at {shown} of {total} characters: ask for less (filters such as status, type or limit, "
               "tail for a log, start_line and max_lines for a file).]",
        "body": "The arguments must be a JSON object, e.g. {\"project\": \"P1\"}.",
        "unknown": "No tool {name} here. Tools: {names}.",
    },
    "fr": {
        "info": "CryoPlug, l'interface de traitement cryo-EM de ce serveur (RELION, imports CryoSPARC, construction de "
                "modèle, affinement, validation) : ses projets, jobs, résultats et journaux, avec les droits du compte "
                "utilisé.",
        "readonly": " Lecture seule : créer, lancer ou arrêter des jobs se fait dans l'interface web de CryoPlug{where}.",
        "ok": "La réponse de l'outil : texte compact pour le modèle (avec des images : JSON avec des data URI).",
        "error": "Ce qu'il faut corriger, ou pourquoi CryoPlug a refusé.",
        "cut": "[… réponse coupée à {shown} caractères sur {total} : demander moins (filtres comme status, type ou "
               "limit, tail pour un journal, start_line et max_lines pour un fichier).]",
        "body": "Les arguments doivent être un objet JSON, ex. {\"project\": \"P1\"}.",
        "unknown": "Pas d'outil {name} ici. Outils : {names}.",
    },
}


def operation_id(name: str) -> str:
    return PREFIX + name


def renamer(names: Iterable[str]):
    """A function writing the names of these tools, in a text, as the assistant knows them (get_job →
    cryoplug_get_job): descriptions and answers mention the tools to call next.  Not inside paths or other words."""
    names = sorted(names, key=len, reverse=True)
    if not names:
        return lambda text: text
    pattern = re.compile(r"(?<![\w./-])(" + "|".join(map(re.escape, names)) + r")(?![\w/-])")
    return lambda text: pattern.sub(lambda m: PREFIX + m.group(1), text)


def clip(text: str, limit: int, language: str = "en") -> str:
    """At most ``limit`` characters (0: no limit), cut at the end of a line, saying how to ask for less."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + "\n" + TEXTS[language]["cut"].format(shown=cut, total=len(text))


def describe(tool: toolmod.Tool, language: str) -> tuple[str, str, dict[str, str]]:
    """Title, description and argument descriptions in the language (English when a translation is missing)."""
    english = {k: v.get("description", "") for k, v in tool.properties.items()}
    if language == "fr" and tool.name in FR:
        title, description, args = FR[tool.name]
        return title, description, {**english, **args}
    return tool.title, tool.description, english


def specification(names: list[str], language: str = "en", public_url: str = "") -> dict[str, Any]:
    """The OpenAPI 3.1 description of these tools (operation paths relative to ``/openapi``)."""
    rename = renamer(names)
    text = TEXTS[language]
    info = text["info"]
    if all(toolmod.TOOLS[n].read_only for n in names):
        info += text["readonly"].format(where=f" ({public_url})" if public_url else "")
    error = {"description": text["error"], "content": {"application/json": {"schema": {
        "type": "object", "properties": {"error": {"type": "string"}}, "required": ["error"]}}}}
    paths: dict[str, Any] = {}
    for name in names:
        tool = toolmod.TOOLS[name]
        title, description, args = describe(tool, language)
        ok: dict[str, Any] = {"text/plain": {"schema": {"type": "string"}}}
        if name == "get_job_images":
            ok["application/json"] = {"schema": {"type": "object", "properties": {
                "text": {"type": "string"}, "images": {"type": "array", "items": {"type": "string"}}}}}
        operation: dict[str, Any] = {
            "operationId": operation_id(name),
            "summary": title,
            "description": rename(description),
            "tags": ["read" if tool.read_only else "write"],
            "responses": {"200": {"description": text["ok"], "content": ok}, "400": error},
        }
        if tool.properties:
            properties = {}
            for arg, schema in tool.properties.items():
                properties[arg] = {**schema, "description": rename(args.get(arg, schema.get("description", "")))}
            operation["requestBody"] = {"required": bool(tool.required), "content": {"application/json": {"schema": {
                "type": "object", "properties": properties, "required": list(tool.required)}}}}
        paths[f"/{name}"] = {"post": operation}
    return {
        "openapi": "3.1.0",
        "info": {"title": "CryoPlug", "version": "1", "description": info},
        "servers": [{"url": BASE}],
        "paths": paths,
    }


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def install(app: FastAPI, config: Config) -> None:
    """Add the OpenAPI tool server (``/openapi``) to the CryoPlug application, if ``[openapi] enabled``."""
    settings = config.openapi
    if not settings.enabled:
        return
    names = toolmod.select_tools(settings.tools, local_files=False)
    rename = renamer(names)
    text = TEXTS[settings.language]
    spec = specification(names, settings.language, config.public_url)
    app.state.auth.public_paths.add(SPEC_PATH)

    @app.get(SPEC_PATH, include_in_schema=False)
    def openapi_specification() -> JSONResponse:
        return JSONResponse(spec)

    @app.post(BASE + "/{name}", include_in_schema=False)
    async def openapi_tool(name: str, request: Request) -> Response:
        if name not in names:
            return _error(text["unknown"].format(name=name, names=", ".join(names)), 404)
        try:
            raw = await request.body()
            arguments = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return _error(text["body"], 400)
        if not isinstance(arguments, dict):
            return _error(text["body"], 400)
        user = getattr(request.state, "user", None) or {}
        log.info("%s called %s (%s)", user.get("username") or "owner", name, ", ".join(sorted(arguments)) or "no argument")
        session = toolmod.Session(AsgiClient(app, request), link_base=config.public_url)
        # In a worker thread: the tool calls the application back (AsgiClient), as /mcp does.
        reply = await run_in_threadpool(toolmod.call, session, name, arguments)
        answer = clip(rename(reply.text), settings.max_answer_chars, settings.language)
        if reply.error:
            status = reply.status if reply.status in (400, 401, 403, 404, 409, 413, 429) else (
                502 if reply.status >= 500 else 400)
            return _error(answer, status)
        if reply.images:
            return JSONResponse({"text": answer, "images": [
                f"data:{mime};base64,{base64.b64encode(data).decode()}" for mime, data in reply.images]})
        return PlainTextResponse(answer)
