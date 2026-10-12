"""Whole-frame native math adapter with a pure-stage fallback.

Reuse the installed CE resolver/mixer. Stateful clip preparation and consumers
remain in PlaybackEngine; a failed C stage never reruns those operations.
"""
import logging

from src.engine.clip_frame_evaluation import evaluate_python

log = logging.getLogger(__name__)


class NativeLayers(list):
    """Resolved layers with an already computed, owned full512 mixed output."""
    def __init__(self, layers, output):
        super().__init__(layers)
        self.output = output


class FrameBatch:
    def __init__(self, native, statistics):
        self.native = native
        self.statistics = statistics
        self.pending = []
        self.failed = False
        self.evaluated = 0
        self.memo = 0

    def add(self, descriptor, complete):
        self.pending.append((descriptor, complete))

    def float_values(self, descriptor):
        try:
            return self.native.float_values([descriptor])[0]
        except Exception:
            self.failed = True
            self.statistics["float_errors"] += 1
            from src.engine.clip_frame_evaluation import _float_math
            return _float_math(descriptor["raw"], descriptor)

    def note_memo(self):
        self.statistics["memo_hits"] += 1
        self.memo += 1

    def flush(self):
        if not self.pending:
            return
        descriptors = [row[0] for row in self.pending]
        try:
            result = self.native.preparedeval(descriptors)
        except Exception:
            self.failed = True
            self.statistics["eval_errors"] += 1
            log.exception("Prepared frame math failed; evaluating only its pure inputs")
            values = [evaluate_python(row) for row in descriptors]
        else:
            self.statistics["eval_batches"] += 1
            self.statistics["eval_tasks"] += len(descriptors)
            self.evaluated += len(descriptors)
            self.statistics["override_tasks"] += sum("override" in row and not row.get("native_transformed") for row in descriptors)
            values = zip(result.data_f, result.data, result.scores)
        for (_, complete), value in zip(self.pending, values):
            complete(value)
        self.pending.clear()

    def resolve(self, maps, gain):
        keys = [(side, universe) for side, mapping in enumerate(maps)
                for universe, rows in mapping.items() if rows]
        groups = [maps[side][universe] for side, universe in keys]
        if not groups:
            self.statistics["no_pending_groups"] += 1
            self.statistics["memo_only_frames"] += int(self.memo > 0 and self.evaluated == 0)
            return ({}, {}), ({}, {})
        result = self.native.resolve_mix(groups, gain)
        self.statistics["resolve_batches"] += 1
        self.statistics["resolve_groups"] += len(groups)
        self.statistics["resolve_candidates"] += sum(map(len, groups))
        resolved, winners = ({}, {}), ({}, {})
        for (side, universe), row in zip(keys, result):
            winners[side][universe] = row.winner
            if row.resolved_layers:
                resolved[side][universe] = NativeLayers(row.resolved_layers, row.output)
        if not self.failed:
            self.statistics["completed_frames"] += 1
            self.statistics["evaluated_frames"] += int(self.evaluated > 0)
            self.statistics["memo_resolved_frames"] += int(self.memo > 0 and self.evaluated == 0)
        return (resolved[0], winners[0]), (resolved[1], winners[1])


def begin_frame(engine):
    factory = getattr(engine, "_ce_prepared_frame_factory", None)
    if factory is None:
        return None
    return factory()


def finish_candidates(frame):
    if frame is not None:
        frame.flush()


def resolve_candidates(engine, frame, maps, python_resolver):
    if frame is not None:
        try:
            return frame.resolve(maps, float(engine._modulation_gain))
        except Exception:
            frame.statistics["resolve_errors"] += 1
            log.exception("Prepared frame resolution failed; resolving only prepared candidates")
    return tuple(python_resolver(mapping) for mapping in maps)


def mix_candidates(engine, items):
    if isinstance(items, NativeLayers):
        return items.output
    return engine._mix_clips(items)
