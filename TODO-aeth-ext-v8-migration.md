# aeth-ext v8 Migration — TODO

Drives the migration of timeclock-entry-processor from aeth-ext 6.2.3 (locked) to 8.0.6+.
Work happens on a dedicated branch (not yet created). **Items are listed in the order they were
decided, NOT implementation order — implementation order will be decided once discussion is done.**

Related: ScheduledReportAggregator's completed migration lives on its `feat/aeth-ext-v8` branch;
its `.claude/plans/aeth-ext-v8-migration-audit.md` is the reference for the consumer-side context.

---

## Implementation order

Code changes land first (while the Windows venv still runs the editable, fixed aeth_ext), the
dependency-source flip lands last so the test environment stays intact throughout.

1. Branch `feat/aeth-ext-v8` off `main`.
2. **Commit A — item 1**: worker-result checking (all-or-nothing), progress restructure (fixes the
   `KeyError: 0` callback bug by keeping the task alive until every future completes), stderr
   failure report with tracebacks, `assert manifest_file` → explicit check.
   - Note resolved during planning: worker mode uses stdlib `logging.handlers.QueueHandler`,
     whose `prepare()` formats `exc_info` into text before pickling — `logger.error(...,
     exc_info=exc)` tracebacks DO cross the queue. Shape sent to SRA: formatted text in the log
     record + full tracebacks on stderr.
3. **Commit B — item 2**: shutdown policy (pool-kill callback at default priority, terminator at
   `LOGGING_TRANSPORT_PRIORITY + 1000`, both `phase=THREADED, required=True`, gated on `-O`;
   `install_signal_handlers=False` in `init_pdf_worker`). Fixed self-termination exit code: 143.
4. **Verify**: `query_logging_configs("timeclock_entry_processor")` returns sane configs; full
   `-O` end-to-end runs on Windows (console exe) and Linux (docker, POSIX wrapper); a
   failure-injection run confirming non-zero exit + empty manifest + stderr tracebacks.
5. **Commit C — item 4**: pyproject floor `aeth-ext>=8.0.6` + registry-only sources (editable
   commented); relock. Venv deliberately NOT re-synced afterwards (registry 8.0.6 lacks the
   static_eval fix until the next aeth-ext release).
6. **SRA branch commit**: delete the dead `remote_per_run.toml` from
   `jobs/timeclock_job/` (item 3).
7. Doc checkbox updates committed on the branch.

Deferred to Jacob afterwards: push + release aeth-ext (with `58afa9b`), bump this floor to that
release and relock, release 2.0.0, bump SRA's pins, SRA runbook step 16 end-to-end.

**Status (2026-08-31): steps 1-7 complete.** Commits: `f69c752` (item 1), `42092ac` (item 2),
`8af0ce7` (item 4) on this branch; `742157e` on SRA's `feat/aeth-ext-v8` (item 3's toml removal).
Verified: clean `-O` end-to-end on Windows exe and Linux container (exit 0, 80 PDFs, manifest
written, 0 FIX_ME / 0 KeyError / 0 ERROR); failure injection (1 rigged store) gives exit 1, NO
manifest, per-store `logger.error` record with traceback, and stderr summary+tracebacks;
`query_logging_configs("timeclock_entry_processor")` resolves program_name + debug/info per-run
remote handlers from aeth-ext's defaults. Only the deferred release-chain steps remain.

---

## 1. Fix swallowed worker-process exceptions (pre-existing bug)

`update_completed_progress` is a `Future.add_done_callback`; the `future.result()` inside it
re-raises *inside the callback*, which `concurrent.futures` logs and discards. `main()` never
checks `proc_futures` itself. A failed week's PDF generation → CLI still exits 0 → manifest still
lists the missing/truncated PDF (entries are added in `process_store_data` *before* generation) →
SRA consumes poisoned output.

**Decision: all-or-nothing.** After the pool drains, check every `proc_future`; if any failed,
report and exit non-zero **before** writing the manifest. SRA sees `CalledProcessError` + empty
manifest → job errors/reschedules → next run regenerates everything. The manifest keeps its
"written last = commit marker" property.

Failure reporting feeds both channels SRA has into this process:

- **Log queue** (primary): `logger.error(..., exc_info=failure)` per failed future with store/week
  identification → central log server via SRA's drainer.
- **stderr** (contract): summary + tracebacks on stderr before non-zero exit → captured by SRA's
  tee into `CalledProcessError.stderr`, so SRA's job layer can decide whether to send a
  job-failed alert even if the log path is degraded.

Notes:

- [x] **Traceback shape resolved**: failures reach SRA as (a) log records whose `exc_info` the
  stdlib QueueHandler formats to text before pickling, and (b) plain-text tracebacks on stderr.
  This program never calls aeth-ext's `alert()` itself — alerting stays SRA's decision — so no
  aeth-ext alert-shape dependency exists.
- [x] Verified: the worker fragment uses stdlib `logging.handlers.QueueHandler`, whose
  `prepare()` formats `exc_info` into text before pickling.
- [x] **Related latent bug found live during the v8 test run (folded into this fix):** `main()`'s
  inner `with progress.add_task(...) as data_task:` block exits (removing the task) while PDF
  futures are still running; every straggler's `update_completed_progress` callback then hits
  `KeyError: 0` in `progress.update(...)` — swallowed by the same done-callback hole (60
  occurrences in one run; output unaffected). Likely introduced by the recent
  "streamline progress and executor management" refactor (`a02b9b0`). The item-1 rework of
  result/callback handling should restructure this so the progress task outlives the futures.

## 2. Shutdown policy: flush logs, then self-terminate

This CLI is effectively idempotent (deterministic overwritten outputs; `manifest.json` written
last as commit marker; no persisted state, no external resources). The v8 shutdown ladder buys no
correctness — so timeclock enforces its *own* policy: on SIGTERM, flush in-flight log records,
then act as if SIGKILLed. A consumer (SRA) standardizing how it stops jobs can never be held
hostage by this subprocess.

**Decision: stay within aeth-ext's framework (option A)** — keep `install_signal_handlers=True`
in the CLI child and register callbacks on v8's shutdown registry. All gated like aeth-ext itself:
active only under `python -O` (`not __debug__`); dev Ctrl+C keeps stock behavior.

- [x] **Pool-kill callback** — `register_for_shutdown(..., phase=ShutdownPhase.THREADED,
  required=True)` at default priority (runs *before* `LOGGING_TRANSPORT_PRIORITY`=1000).
  Immediately terminates the process pool so workers stop generating new records and stop burning
  CPU on results that are now useless (cancelled mid-run = regenerated next run).
  - `executor.shutdown(wait=False, cancel_futures=True)` only drops queued work; to stop a worker
    mid-PDF, kill the worker processes — via `executor._processes` (private but stable, use
    defensive `getattr`) or PIDs reported from `init_pdf_worker`.
  - Executor only exists inside `main()`'s `with` block: register at creation, reach it through a
    module-level ref cleared on normal completion (late signal after pool close = no-op).
  - Callback signature takes `trails: tuple[ExceptionTrail, ...]` per the v8 contract.
- [x] aeth-ext's own logging-transport teardown at priority 1000 then flushes the mp-queue feeder
  (child-side `queue.close()` + `join_thread()` semantics — worker-buffered records may drop;
  accepted).
- [x] **Terminator callback** (exit code 143) — `required=True`, `priority > LOGGING_TRANSPORT_PRIORITY`, calls
  `os._exit(<fixed non-zero code>)` so nothing after the flush can stall. Pick a fixed code so
  SRA can distinguish "told to stop" from "crashed". (Manifest stays unwritten either way.)
- [x] **Workers**: `init_pdf_worker` passes `install_signal_handlers=False` unconditionally —
  pool workers are executor-owned and must not run their own shutdown ladder.

## 3. Remote logging config: keep aeth-ext's default

**Decision: no config provider in this package.** When SRA's drainer runs
`query_logging_configs("timeclock_entry_processor")`, aeth-ext's constants-derived fallback
(`PROJECT_NAME`, `LOGGING_TYPE = "per_run"`, `TESTING` from `__main__.py`) is the intended path —
this project is the example case that drives aeth-ext's remote per-run default, and standard
changes in aeth-ext should not need mirroring here.

- [x] Verified: `query_logging_configs("timeclock_entry_processor")`'s spawn-import fallback resolves
  `PROJECT_NAME`/`LOGGING_TYPE` correctly from our `__main__.py` (this is exactly the path SRA's
  drainer exercises; depends on our package import staying side-effect-free — keep the 1.10.2/1.10.3
  lazy-resolution invariant).
- [x] **Done (`742157e` on SRA's branch): deleted the vestigial
  `src/scheduled_report_aggregator/jobs/timeclock_job/remote_per_run.toml` and commit the removal
  to that branch.** Its header references a sibling `remote_daily.toml` — check whether that one
  is equally dead and remove it in the same commit if so.

## 4. Dependency pin & sources

**Decision:** `aeth-ext>=8.0.6`, sourced from SFTPyPI only, so Linux and Windows lock the same
published version (today's lock is split: 6.2.3 registry on Linux / 8.0.4 editable on Windows).
The floor bump is what stops any consumer from resolving a pre-v8 aeth-ext next to a post-migration
timeclock.

- [x] Bump `dependencies` floor to `aeth-ext>=8.0.6` (must rise again to the release carrying
  `58afa9b` before 2.0.0 ships).
- [x] `[tool.uv.sources]`: registry-only; keep the editable `../aeth_ext` source as commented-out
  lines for quick switching during aeth-ext dev/testing (same pattern as SRA's pyproject).
- [x] Relock (`uv lock`) and confirm both platform resolutions land on the same aeth-ext version.

## 5. Release: major version bump

**Decision: 2.0.0** — per the standard used for all the other projects' v8 migrations. The CLI
contract is unchanged, but the aeth-ext floor jump makes this un-co-installable with pre-v8
environments; the major bump marks the v8-era line.

- [ ] At end of migration: bump to 2.0.0 and publish to SFTPyPI (via the devkit release task).
- [ ] Verify the devkit release task exists/works before relying on it (the old `scripts/release.sh`
  is deleted).
- [ ] Cross-project: SRA then bumps to `timeclock-entry-processor>=2.0.0` and relocks on its branch.

## 6. `TaggedLogRecord` PROJECT_NAME resolution failure (confirmed by test, 2026-08-31)

v8 raises `ValueError("Expected project name to be set, but got 'FIX_ME'")` on the first log call
when `PROJECT_NAME` resolution misses (v6 silently tagged `FIX_ME`). **Confirmed live on Windows:**
running the real console-script exe under `PYTHONOPTIMIZE=1` crashes the **main CLI process** on
its very first `logger.info` in `main()` — before any pool worker is involved.

**Root cause (in aeth-ext, not here):** the Windows launcher re-invokes Python with
`sys.argv[0]` = the script path **without** the `.exe` extension, while `__main__.__file__` is the
virtual `…\timeclock-entry-processor.exe\__main__.py`. `get_entrypoint_root`'s console-script
redirect gate requires `argv[0]` to equal the real ancestor file (the `.exe`) — the extension
mismatch fails the gate, the redirect is skipped, the ceiling resolves to the virtual exe dir,
no `PROJECT_NAME` is found → `FIX_ME` → raise. (`initialize()`'s own config path is unaffected —
it resolves `caller_file` from the calling frame; only `TaggedLogRecord`'s independent re-resolution
walks from `__main__`.)

- [x] **Fix implemented in aeth-ext and committed as `58afa9b` on its `main`** (`static_eval.py`),
  verified on BOTH platforms, four parts:
  1. `_matches_invocation()` helper: the console-script redirect gate accepts the Windows
     launcher's extensionless `argv[0]` (`argv[0] + ".exe" ==` real ancestor), win32-only.
  2. **Spawn workers (Windows)**: a spawned pool worker's `__main__` has no `__file__` *and no
     `__spec__`*, so resolution fell to `_resolve_root_without_main_file` strategy 3
     (`dirname(argv[0])` = the venv `Scripts/` dir — no app code there). Strategy 3 now mirrors
     the parent's gate: if `argv[0]` (± `.exe`) is a real console-script wrapper in this
     interpreter's scripts dir, redirect via `_resolve_console_script_entrypoint()`.
  3. **Forkserver workers (Linux — production!) failed for a third reason**: the gate demanded
     `argv[0] ==` the ancestor even when `main_file` came from the real `__main__.__file__`.
     A forkserver child inherits a correct `__main__.__file__` but its `argv[0]` can be the
     multiprocessing bootstrap's `-c` placeholder. The argv agreement is now only required for an
     *explicit* `main_file` override (the test-suite false-positive case the gate exists for).
  4. **Stale `argv` module binding**: `from sys import argv` captures the list object at import
     time; the forkserver server imports aeth_ext with `sys.argv = ['-c']`, children fork from it,
     and `multiprocessing.prepare()` then *rebinds* `sys.argv` — leaving static_eval reading the
     stale `['-c']` list forever (resolver looked up entry point `-c` → None). All call-time argv
     reads now go through `sys.argv`. (This was a Heisenbug: instrumented runs shifted import
     timing and passed.)
  Remaining: commit + release on the aeth-ext side (bump past 8.0.6), then this project's floor
  moves to that version (updates item 4's `>=8.0.6`). Consider aeth-ext regression tests for the
  three worker scenarios (win32 spawn, linux forkserver, console-script parent).
- Hypothetical timeclock-side workaround if ever needed (it shouldn't be): one line at the top of
  `__main__.py` normalizing `sys.argv[0]` (append `.exe` when `Path(argv[0] + ".exe").is_file()`).
  Trivial — but the aeth-ext fix is small, so this stays unused. (Would NOT cover the Linux
  forkserver failures — those genuinely require the aeth-ext fix.)
- [x] Windows verification (2026-08-31): `PYTHONOPTIMIZE=1` + real console-script exe + sample
  CSV → full clean run: 0 `FIX_ME`/`ValueError`, 80 PDFs, main process and all pool workers
  resolve `timeclock_entry_processor`.
- [x] Linux verification (2026-08-31): docker `ghcr.io/astral-sh/uv:python3.14-bookworm`, local
  aeth_ext + timeclock installed as wheels into a fresh venv, run via the POSIX console script
  under `PYTHONOPTIMIZE=1` → exit 0, 0 `FIX_ME`/`ValueError`, 80 PDFs. (Same run confirmed the
  item-1 progress `KeyError` bug fires on all 80 callbacks on Linux.)
- Side-finding from the same test: under `-O`, aeth-ext's `BaseSettings` requires
  `ALERTS_EMAIL_PWD` from real env vars at **import time** (`.env` is debug-only). Fine in SRA's
  container (compose provides it; subprocess inherits `environ`), but any bare `-O` invocation
  without it dies before `main()`. Known aeth-ext design; noted for test harnesses.

---

## Out of scope (decided)

- **Devkit project-standardization pass** (inline pyright/ruff config, tombi, .gitattributes,
  .gitignore/launch.json updates): handled separately by Jacob once the devkit command's design
  and implementation is finished iterating.

## Already done (on main, outside the branch)

- `220269f` — deleted vestigial `scripts/release.sh` (predates poe_tasks; rollback path deleted
  the wrong package — `aeth-ext` — from SFTPyPI) and fixed `[tool.coverage.run] source_pkgs` to
  `timeclock_entry_processor`.
