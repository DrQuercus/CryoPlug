"""Decision map of a job: the steps it took and the choices it made, as tiles joined by lines.

The panel draws it as a mind map from the inputs down: the runs of a round side by side in one box, each decision under
what it was taken from, the path that leads to the result lit up and the branches tried and left aside faded. The map
is saved into the report whenever it changes, so it grows while the job runs.

A node is ``{id, kind, title, status, parents, lines, detail, view, group, icon}``:

- ``kind``: ``input`` (what the job starts from), ``map``, ``run`` (one run of a program: a model), ``group`` (a box
  holding the runs of a round), ``decision`` (a choice the job made) or ``result``;
- ``status``: ``done``, ``running``, ``chosen`` (kept by the decision that follows), ``discarded`` (tried, not kept),
  ``failed``, ``skipped`` (not needed) or ``warning``;
- ``parents``: the nodes it follows from (a line from each); the runs of a box have none of their own, the box has;
- ``lines``: two or three short lines shown on the tile, ``detail``: the full text shown when the tile is clicked;
- ``view``: maps and models to open in the 3D viewer; ``icon``: one of the interface's icons.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cryoplug.jobs.base import JobContext

KINDS = ("input", "map", "run", "group", "decision", "result")
STATUSES = ("done", "running", "chosen", "discarded", "failed", "skipped", "warning")


class DecisionMap:
    def __init__(self, ctx: JobContext, title: str = "Decision map", key: str = "decision_map", note: str = ""):
        self.ctx, self.title, self.key, self.note = ctx, title, key, note
        self.nodes: dict[str, dict[str, Any]] = {}

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def add(self, node_id: str, kind: str, title: str, parents: Iterable[str] = (), lines: Iterable[Any] = (),
            status: str = "done", detail: str = "", view: Iterable[dict[str, Any]] = (), group: str = "",
            icon: str = "") -> str:
        """Add a step (or replace the one with this id, in its place). Unknown parents are left out."""
        if kind not in KINDS:
            raise ValueError(f"Unknown kind of step {kind!r}")
        node: dict[str, Any] = {"id": node_id, "kind": kind, "title": title, "status": self._status(status),
                                "parents": [p for p in dict.fromkeys(parents) if p in self.nodes and p != node_id]}
        if group and group in self.nodes:
            node["group"] = group
        self._fill(node, lines=lines, detail=detail, view=view, icon=icon)
        self.nodes[node_id] = node
        self.save()
        return node_id

    def update(self, node_id: str, **changes: Any) -> None:
        """Change the status, title, lines, detail, view or parents of a step already on the map."""
        node = self.nodes.get(node_id)
        if node is None:
            return
        if "status" in changes:
            node["status"] = self._status(changes.pop("status"))
        if "title" in changes:
            node["title"] = changes.pop("title")
        if "parents" in changes:
            node["parents"] = [p for p in dict.fromkeys(changes.pop("parents")) if p in self.nodes and p != node_id]
        self._fill(node, **changes)
        self.save()

    def mark(self, chosen: str | None, others: Iterable[str]) -> None:
        """A decision kept ``chosen`` and left the ``others`` aside (those that ran: failed runs stay failed)."""
        for node_id in others:
            node = self.nodes.get(node_id)
            if node and node_id != chosen and node["status"] in ("done", "chosen"):
                node["status"] = "discarded"
        if chosen in self.nodes:
            self.nodes[chosen]["status"] = "chosen"
        self.save()

    def save(self) -> None:
        try:
            self.ctx.add_flow(self.title, list(self.nodes.values()), key=self.key, note=self.note)
        except Exception as exc:  # the map never stops the job
            self.ctx.log(f"(decision map not saved: {exc})")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _status(status: str) -> str:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}")
        return status

    def _fill(self, node: dict[str, Any], lines: Iterable[Any] | None = None, detail: str | None = None,
              view: Iterable[dict[str, Any]] | None = None, icon: str | None = None) -> None:
        for name, value in (("lines", None if lines is None else [str(x) for x in lines if x not in (None, "")]),
                            ("detail", detail), ("icon", icon),
                            ("view", None if view is None else self.ctx.view_items(view))):
            if value is None:
                continue
            if value:
                node[name] = value
            else:
                node.pop(name, None)
