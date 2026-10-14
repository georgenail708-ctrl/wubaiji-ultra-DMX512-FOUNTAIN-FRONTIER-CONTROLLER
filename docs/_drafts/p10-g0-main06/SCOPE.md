# Genuine ordinary Main grid

Source-only ROOT window; old G0/R1/05 sources/oracles are unchanged.
Read PE _project_timeline_end1085–1096, ordinary frame cache1127–1130 and frame
range1296–1310. Reuse exact original semantics, not the filtered native schedule.

R6 compiler receives every authored native raw clip's start and duration atoms
with actual native row stride/offsets and actual owned raw QTimer ms (or absent
default23). The ABI reads strided atoms without allocating/copying all timing
rows. End/clamp/count/positions are wholly C. No audio end is added here. Muted,
folder, solo-excluded and zero/negative-duration late clips still affect end.

Known raw atoms match existing R1 ABI16: missing0/f64bits1/i64bits2/bool3/None4.
Exact ordinary types are handled without host float coercion, including NaN,
infinity and signed zero at their original max/or/ceil stages. Unknown/string/
arbitrary-int conversion domains return explicit PENDING before any result write;
do not label those domains supported or call them legitimate conversion failures.
Actual Python ceil overflow selects its original exception rather than fallback.
Finite counts beyond uint64 are explicit BIG_COUNT ownership/capacity refusal;
original arbitrary Python ints are not silently truncated or called equivalent.

Request pointer/stride/extent and exact typed suppliers must be admitted through
R6 current source+actual shared reservation before dispatch. Native validates
declared bounds/aliases but cannot prove pointer accessibility. No heap/thread/
IO/clock calls. Main ordinary root count and positions must supersede the old
R4 filtered/timer grid only after actual complete compile. Export/incremental
grids remain separately unfinished and are not relabelled as this ordinary route.

Future UNRUN controls: empty project, absent timer, timer clamp boundaries,
zero/negative-duration late clip, muted/folder end, decimal clip end and final
clamped position, NaN/or-zero/max ordering, true ceil infinity exception,
unknown atom refusal/no writes, invalid pointer/alignment/alias/stride/extent,
finite huge count refusal, same real two shows11889/full other count and every
raw-f64 original position, both installed ISA paths. No helper/native/build/test.
