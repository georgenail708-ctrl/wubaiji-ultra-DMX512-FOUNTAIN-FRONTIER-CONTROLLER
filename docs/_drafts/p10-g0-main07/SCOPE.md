# G0 Main07 bounded native compile successor

SOURCE_ONLY_UNCOMPILED_UNRUN. ROOT owns these new files; all Main06 sources and
controls stay unchanged. Reuse Main06 exact raw atom conversions/grid formulas.
No helper/Python/AST/hash/import/build/native/Qt/tests/render under current hold.

Actual gap: Main06 compile scans every authored row in one entry. R6 cooperative
compile calls it one phase but cannot count an unbounded scan as one unit. Main07
adds fixed, caller-owned compile state: begin only validates/copies raw request;
step consumes each actual authored row and final interval/ceil as separate units.
Zero units does not progress, each call caps units at64, grid publishes only on
successful finalization. Errors keep original Grid untouched. No new heap/thread/
callback, and no host interval/end/count/position math. The original main06
functions remain exported unchanged; no source or semantic oracle replacement.

Caller is the genuine R6 owner: state bytes are charged with the actual R6 job,
raw rows retained under the existing source/current/reservation contract, cancel
is checked between native compile units. Native pointer/layout validation is not
source authentication. Do not take copied state/constant-True callbacks as proof.
Final cache/source/temporal publication stays withheld until all R6 domains pass.

Required UNRUN controls: zero units, single-row prefix, last-row versus final
unit, terminal repeat, rejection before writes, same every-original G0 result/
exception selector, infinity/BIG_COUNT, malformed rows, actual-source change/
cancel and released owner. Both ISA/manual layouts/full original show grids and
installed Main/output acceptance are mandatory before qualification.
