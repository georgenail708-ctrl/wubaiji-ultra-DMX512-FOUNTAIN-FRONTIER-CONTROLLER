# R216 component fixture import adaptation

Non-integrated fountain projects can represent one physical unit using several
patched fixtures. Import now retains those physical identities and addresses
while reusing the existing logical device model for selection and operation.
No project names or manufacturer-specific profiles are used as detection rules.

## Resulting behavior

- A nested complementary X-only/Y-only pair shares an import family and logical
  package. Independent axis controls are retained; nearby or sibling fixtures
  are not paired. Integrated fixtures retain their existing behavior.
- Repeated views of a package share a deterministic logical ID and members are
  deduplicated for both dictionary and object records. The actual parser's
  PatchRows are bridged to fixture instance IDs before existing XY detection.
- Centre, speed and control-mode sources remain functional channels even when
  their first capability names an axis. Bound continuous generic angle sources
  can participate in existing motor controls. Import restores original binding
  metadata when a fast XML loader omitted it.
- Explicit internal physical slot gaps are retained. Virtual channels do not
  collapse later physical slots. Leading offsets, coarse/fine pairs, original
  defaults, mode labels and multihead channel suffixes remain intact.
- Linker unit matching recognizes the existing package, composite and unit
  representations. Pump masks and authored base values retain their existing
  meaning.
- Open arcs remain open, including large arcs crossing the angle origin. The
  existing closed-ring evidence and world-to-canvas projection are shared.
- Child positions include the original parent height, scale and 3D rotation.
  Direct parent quaternions take priority over stale Euler fields. Existing
  installation orientation and motion calculations are unchanged.

## Bounded acceptance

The supplied private real project was imported through the actual Main menu
and patch completion button, then exercised through Programmer, batch Linker
creation, actual network-manager output and UDP ArtDmx capture. It retained
751 physical fixtures and 13 definitions. Repeated package records reduced
from 681 to 273 without losing physical members; 40 nested XY pairs were
controlled independently. Twenty batch rules drove twenty pumps, and 42 centre
channels used their original physical third slot.

Actual Main save/reopen retained all 751 fixtures, 273 packages and 20 rules.
All 751 world XZ positions matched an independent original-data 3D matrix
calculation after reopening. The broader inventory covered 80 device files,
104 distinct modes and 586 source records. It found no missing physical slots,
nonadjacent fine-slot loss, actual patch overlaps, instance mode-cache collision
or type-metadata collision in these inputs. Unknown capabilities and missing
physical calibration remain explicit functional/unknown data rather than guessed
motion profiles.

Focused acceptance: **204 passed, 2 skipped** in 5.03 seconds. Skips refer to an
older real fixture project unavailable at its historical path. Current real
Main acceptance passed with normal exit; the final guarded run took 18.49
seconds for the complete interaction/save/reopen journey. This duration is not
a render-speed measurement. Synthetic tests are independently authored and
contain no proprietary fixture definitions. The composed public implementation
also passed all 24 standalone component tests without importing either private
format reader.

Design checks passed. Whole-workspace architecture checks still report 34
pre-existing errors in the earlier CE/SNTC/Main work. Current import changes add
no architecture errors; the frozen baseline was not changed. Full pytest,
whole-show Effect parity and physical Depence/hardware acceptance were not
rerun. CE completion remains delegated to dot, as recorded in the R215 handoff.
No release package was produced.

## References and publication boundary

Reused implementations: the existing logical-device and parent-chain models,
channel definitions, shared geometry classification, Main import/save path,
Linker engine and installed SciPy Rotation. No upstream code was copied.

- [GDTF DMX offsets, resolutions and combined fixture breaks](https://gdtf-development.com/help/users/gdtf_builder/dmx/index.html), documentation version 54bca65.
- [OFL channel model](https://github.com/OpenLightingProject/open-fixture-library/blob/master/lib/model/CoarseChannel.js), reviewed blob 024b2c7a73896b74d969d6350fc677db5699f320; MIT.
- [OFL fine-channel identity](https://github.com/OpenLightingProject/open-fixture-library/blob/master/lib/model/FineChannel.js), reviewed blob d87ffdd2d211fe7c3c035d1e86a5a6f6f23f5a59; MIT.
- [SciPy Rotation.apply](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.transform.Rotation.apply.html) and [scalar-last quaternion convention](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.transform.Rotation.from_quat.html); existing SciPy dependency, BSD-3-Clause.

Only general implementation changes, synthetic regression tests and this
sanitized note are public. The existing private format readers were already
absent from the public repository and remain local, with their adapter wiring
and complete real-project evidence. General role/slot and world-transform
helpers accept decoded metadata and never read encrypted files or keys.
Original projects/libraries, extracted data and private probes remain local.
Existing public-source cleanup is retained by applying only the reviewed R216
delta to the actual PR14 parent. The change does not merge, rewrite or supersede
dot's other open work.
