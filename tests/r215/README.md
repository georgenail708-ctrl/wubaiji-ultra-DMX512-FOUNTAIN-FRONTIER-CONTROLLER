# Focused synthetic CE regression tests

These tests use small in-memory model projects, complete DMX frames, real CE functions, and frozen original public MIT source. They do not need showfiles, fixture-library assets, user settings, a Depence installation, network receivers, or other test packages. Filenames retain their original draft identities so their test functions and assertions can be compared to the independently reviewed controls.

The suite is a focused regression contract. It does not measure whole-show speed, prove a full release, or replace MainWindow/output/persistence acceptance.

## Supported test environment

Run in a fresh Windows Python 3.13 process with the project dependencies and pytest installed. The reviewed NumPy source version is **2.4.4**. The strict source checks deliberately deny suppliers from unreviewed versions rather than treating them as native success. Both current `render_lab/qascade_ce/ce_engine_sse2.dll` and `ce_engine_avx2.dll` builds, their matching C source, opaque symbol map and the relevant R215 product modules must be present. The ABI batch asserts both DLLs exist; it never skips the missing build and calls that a pass.

From the repository root, activate the project environment, then set the bounded test process environment:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONUTF8 = '1'
```

Run batches separately while the corresponding product changes are being published:

```powershell
python -m pytest -q tests/r215/test_r215_c05_generic_abi_draft.py tests/r215/test_r215_c05_qualification_tamper_draft.py tests/r215/test_r215_c05_metadata_edges_draft.py tests/r215/test_r215_cross_track_native_candidate.py
python -m pytest -q tests/r215/test_filter_preparation_draft.py tests/r215/test_filter_preparation_qualification_draft.py
python -m pytest -q tests/r215/test_r215_fade_target.py tests/r215/test_r215_fade_target_guard.py tests/r215/test_r215_fade_target_review.py
```

The expected parameterized node counts are C05 **114** (72 dual-CPU ABI, 32 source/currentness, 7 layout/map edge, 3 actual full512/dependencies/native-work); C02 **60** (32 original numerical/warning/cache controls and 28 strict admission controls); C03 **50** (30 original full-frame/native/cache controls, 11 wrapper/current controls, 9 independent initialization/provenance controls). These counts describe coverage, not an execution result. Once all matching product changes are present, `python -m pytest -q tests/r215` runs the combined 224-node scope.

## Fixture and reference contract

`r215_support.py` contains only four existing synthetic helpers: a 512-value/active-mask payload, actual PlaybackEngine/NetworkManager construction, a four-track/eight-fixture synthetic project, and owned CE mounting/cleanup. It imports no older test module. Real model defaults and output layers are used; there is no manufactured native result or replacement production numerical path.

`conftest.py` owns one QApplication through real background-resource shutdown and isolates QSettings in temporary INI files before product imports. It calls the actual shutdown functions of already loaded audio-analysis and bake modules and restores the settings class, aliases and INI paths at process finish. GC and the actual audio-analysis start method retain their normal behavior. CE selection is owned by the actual mounting fixtures and mount defaults; the setup does not set a CE environment override. CE calculations, source admission, native ABI and all output assertions remain intact.

The three reference files contain original public functions from the repository's MIT source. Public source AST provenance is recorded in the accompanying publication review; the byte hashes inside the tests remain the original independent pins. C03 freshness tests deliberately create a fresh original oracle for each public NetworkManager update so an original weak cache cannot hide changed output.

The metadata edge/foreign cases never invoke modified native pointers. The normal cases require successful actual C work, full output bits/shape/ownership where specified, source/currentness qualification, unchanged warnings/exceptions and precise cache ownership. A collection error, unavailable dependency/build, native crash or nonzero process exit is not successful acceptance.
