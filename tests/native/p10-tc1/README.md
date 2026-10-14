# Bounded native timecode controls

The C includes here are exact authored product source copies. `clock_test.c`
composes them into one test-only library for raw packet, authority, duplicate,
freshness, alias, overflow and current-marker controls. The original independent
test packet literals/expectations remain unchanged. The ABI binding changes only
the old research `qce_` spelling to the actual product `ce_` entry names.

`reference_pending_controls.c` checks that a new external STOP supersedes a
queued PLAY, a new PLAY supersedes a queued PAUSE, matching requests coalesce,
and a held position does not invent a pause. No proprietary binary, disassembly,
original show or private cache is included.

The standard Windows CI job runs serially for SSE2 and AVX2 inside the existing
Windows Job guard: at most 1.25 GiB and at least 8 GiB free physical/commit/disk.
Its raw exits, compiled source/binary hashes and owned-resource outcomes are
retained alongside grid controls. This library does not install or replace the
product CE DLL. Passing it does not establish MainWindow/UDP/Qt/source admission,
cache preservation, reference-player IO or final DMX output acceptance.
