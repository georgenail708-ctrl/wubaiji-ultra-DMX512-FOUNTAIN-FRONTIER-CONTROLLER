# Bounded native timeline bake grid candidate

Main06 reads original all-authored clip timings and computes the exact ordinary
Main end, interval, frame count and clamped positions. Main07 keeps that contract
while compiling one authored row or finalization per bounded unit. The original
Main06 source/controls remain separate; Main07 includes the same original C file
once. This adds no product installer, playback backend or default setting.

Status at submission: source candidate; hosted compilation/controls pending.
The workflow tests exact authored source with SSE2 and AVX2, preserves original
independent expectations and compares actual C/ctypes sizes/offsets. It reuses
the reviewed project resource guard in r215_guard (original source bytes copied,
not a replacement guard implementation). The helper location isolates its own
validation lock inside this test fixture. Native output stays in the checkout.
Each job requires1.25GiB maximum and8GiB physical/commit/disk reserves, process8,
log16MiB and one serial task. Failures/STOP remain failures.

A passing native run is evidence for this G0 candidate only. R6 complete ordinary
producer/context/cached state integration, full independent original show
equivalence, Main/output/normal-close and equal-work performance are unfinished.
No complete CE or10second render result is represented by these files.

References: the original project PlaybackEngine timeline-end/bake/range bodies;
GitHub standard hosted runner documentation; Python/Windows owned resource
semantics already documented by the original guard. Public copies contain only
authored source/controls and no private show, binary or reverse-engineering data.
