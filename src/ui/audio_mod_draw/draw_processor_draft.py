"""AM23 processor working state on the original Draw undo stack.

Reuse: Draw's QUndoCommand double snapshots and original Apply boundary.
Runtime schema/clamping belongs to engine.audio_mod_processor_schema; no DSP here.
"""
from __future__ import annotations

from copy import deepcopy
import uuid
import weakref

from PyQt6 import sip
from PyQt6.QtGui import QUndoCommand


def catalog():
    from ...engine.audio_mod_processor_schema import PROCESSOR_CATALOG
    return PROCESSOR_CATALOG


def normalized(rows):
    from ...engine.audio_mod_processor_schema import normalize_processors
    return normalize_processors(rows)


class _ProcessorEdit(QUndoCommand):
    def __init__(self, draft, before, after, text):
        super().__init__(text)
        self._draft = draft
        self._before, self._after = deepcopy(before), deepcopy(after)

    def undo(self):
        self._draft.restore(self._before)

    def redo(self):
        self._draft.restore(self._after)


class ProcessorDraft:
    def __init__(self, panel):
        self._panel = weakref.ref(panel)
        self.mode = "legacy"
        self.rows = []
        self.present = False

    def snapshot(self):
        return deepcopy((self.mode, self.rows, self.present))

    def reset(self, state):
        raw = state if isinstance(state, dict) else {}
        self.mode = "chain" if raw.get("processor_mode") == "chain" else "legacy"
        self.rows = normalized(raw.get("processors", []))
        self.present = "processors" in raw or "processor_mode" in raw

    def restore(self, snapshot):
        self.mode, self.rows, self.present = deepcopy(snapshot)
        panel = self._panel()
        if panel is not None and not sip.isdeleted(panel):
            panel._processor_chain.refresh()
            panel._on_graph_changed()

    def commit(self, mode, rows, text):
        panel = self._panel()
        if panel is None or sip.isdeleted(panel):
            return
        after = (mode, normalized(rows), True)
        before = self.snapshot()
        if before == after:
            return
        panel._canvas._undo.push(_ProcessorEdit(self, before, after, text))

    def payload(self):
        if not self.present:
            return {}
        return {"processor_mode": self.mode, "processors": deepcopy(self.rows)}

    def set_enabled(self, enabled):
        self.commit("chain" if enabled else "legacy", self.rows, "启用处理链" if enabled else "停用处理链")

    def add(self, kind):
        spec = catalog()[kind]
        params = {key: deepcopy(field.get("default"))
                  for key, field in spec.get("params", {}).items()}
        row = {"id": uuid.uuid4().hex, "kind": kind, "enabled": True,
               "wet": 1.0, "source": spec["source"],
               "targets": list(spec["targets"]), "params": params}
        self.commit(self.mode, [*self.rows, row], "添加处理器")
        return row["id"]

    def edit(self, row_id, values):
        rows = deepcopy(self.rows)
        for row in rows:
            if row.get("id") == row_id:
                row.update(deepcopy(values))
                self.commit(self.mode, rows, "编辑处理器")
                return

    def remove(self, row_id):
        self.commit(self.mode, [row for row in self.rows if row.get("id") != row_id],
                    "删除处理器")

    def move(self, row_id, delta):
        rows = deepcopy(self.rows)
        index = next((i for i, row in enumerate(rows) if row.get("id") == row_id), -1)
        target = index + delta
        if index >= 0 and 0 <= target < len(rows):
            rows[index], rows[target] = rows[target], rows[index]
            self.commit(self.mode, rows, "调整处理顺序")
