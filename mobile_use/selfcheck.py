"""``mobile-use selfcheck`` — self-validation of the perception/training + action surface.

Device-free. Three checks, so a broken optional dep or a drifted action list is caught
BEFORE a real run rather than silently at perceive/act time:

  1. dep-rung matrix  — which local-grounding rungs are available, and WHY not;
  2. action surface   — no phantom verbs, no duplicates (the D5 drift guard, at runtime);
  3. training smoke   — synthetic dataset -> build_yolo_dataset -> ground via the template
                        matcher (pure-Pillow path always runs; opt-in ``--train`` also runs
                        a 1-epoch real YOLO train + load when the [yolo] extra is present).

Exit 0 iff the core invariants hold (action surface consistent AND the training smoke
builds a non-empty dataset). Missing OPTIONAL deps are reported, not failed.

Distinct from ``mobile-use --doctor`` / ``mobile-use doctor`` (those check DEVICE
connectivity); this checks the harness's own internal consistency.
"""
import os
import sys
import tempfile

_OK = "✓"
_NO = "✗"


def dep_rung_matrix():
    """Each local-grounding rung as ``(name, available: bool, detail)``."""
    rungs = []

    # Rung 1: trained YOLO detector (ultralytics + a loadable weights file).
    try:
        from mobile_use.train_detector import YoloDetector
        from mobile_use.train_detector import available as yolo_dep
        weights = os.environ.get("MU_DETECTOR_WEIGHTS")
        if not yolo_dep():
            rungs.append(("yolo_detector", False,
                          "ultralytics not installed (`pip install 'mobile-use[yolo]'`)"))
        elif not weights:
            rungs.append(("yolo_detector", False,
                          "ultralytics present but MU_DETECTOR_WEIGHTS not set"))
        else:
            ok = YoloDetector(weights).available()
            rungs.append(("yolo_detector", ok,
                          "ready" if ok else f"weights {weights} missing or not loadable"))
    except Exception as e:  # never let the report itself crash
        rungs.append(("yolo_detector", False, f"error: {type(e).__name__}: {e}"))

    # Rung 2: OpenCV template matcher.
    try:
        from mobile_use.local_detector import available as cv_dep
        ok = cv_dep()
        rungs.append(("template_matcher", ok,
                      "ready (opencv)" if ok
                      else "opencv not installed (`pip install 'mobile-use[detection]'`)"))
    except Exception as e:
        rungs.append(("template_matcher", False, f"error: {type(e).__name__}: {e}"))

    # Rungs 3 + 4: tree + VLM are structurally always-on (runtime needs a device / API key).
    rungs.append(("accessibility_tree", True, "always available (device-dependent at runtime)"))
    rungs.append(("vlm_fallback", True, "always available (needs ANTHROPIC_API_KEY at runtime)"))
    return rungs


def action_surface_issues():
    """List action-surface inconsistencies (empty list == consistent)."""
    issues = []
    try:
        import android_harness.helpers as ah
        import iphone_harness.helpers as ih
        from mobile_use.agent_loop import ACTION_VERBS
        from mobile_use.record_replay import RECORDED_HELPERS

        def _verbs(mod):
            return {n for n in dir(mod) if not n.startswith("_") and callable(getattr(mod, n))}

        ios, anh = _verbs(ih), _verbs(ah)
        for v in ACTION_VERBS:
            if v not in ios and v not in anh:
                issues.append(f"ACTION_VERBS lists a verb on no platform: {v!r}")
        for v in RECORDED_HELPERS:
            if v not in ios and v not in anh:
                issues.append(f"RECORDED_HELPERS lists a verb on no platform: {v!r}")
        if len(ACTION_VERBS) != len(set(ACTION_VERBS)):
            issues.append("ACTION_VERBS has duplicate entries")
    except Exception as e:
        issues.append(f"could not introspect action surface: {type(e).__name__}: {e}")
    return issues


def training_smoke(run_train=False):
    """Device-free training-pipeline smoke. Returns ``(ok: bool, detail: str)``.

    Always: synthetic seed -> build_yolo_dataset (non-empty) -> ground via template matcher
    (when opencv present). With ``run_train`` AND ultralytics present, also runs a bounded
    1-epoch real train + ``validate_weights`` to exercise the full distillation path.
    """
    try:
        from mobile_use.synthetic_ui import generate_seed_dataset
        from mobile_use.train_detector import build_yolo_dataset
        with tempfile.TemporaryDirectory() as d:
            samples = generate_seed_dataset(os.path.join(d, "seed"), n=3, seed=0)
            if not samples:
                return False, "synthetic generator produced no samples"
            stats = build_yolo_dataset(samples, os.path.join(d, "ds"))
            if stats["images"] < 1 or stats["boxes"] < 1:
                return False, (f"built dataset is empty: {stats['images']} images, "
                               f"{stats['boxes']} boxes")
            detail = f"dataset {stats['images']} imgs / {stats['boxes']} boxes"

            try:
                from mobile_use.local_detector import LocalElementMatcher
                from mobile_use.local_detector import available as cv_dep
                if cv_dep():
                    m = LocalElementMatcher.from_samples(samples)
                    s0 = samples[0]
                    hit = m.locate(s0["screenshot"], label=s0["label"])
                    detail += (f"; template matcher {m.template_count} templates, "
                               f"{'grounded OK' if hit else 'ground miss (non-fatal)'}")
                else:
                    detail += "; template ground skipped (no opencv)"
            except Exception as e:
                detail += f"; template ground skipped ({type(e).__name__})"

            if run_train:
                try:
                    from mobile_use.train_detector import available as yolo_dep
                    from mobile_use.train_detector import train, validate_weights
                    if yolo_dep():
                        res = train(stats["dataset_dir"], epochs=1,
                                    project=os.path.join(d, "runs"))
                        verified = res.get("status") == "trained" and validate_weights(
                            res.get("weights"))
                        detail += (f"; real train status={res.get('status')}, "
                                   f"verified={bool(verified)}")
                    else:
                        detail += "; real train skipped (no ultralytics)"
                except Exception as e:
                    detail += f"; real train errored ({type(e).__name__}: {e})"
            return True, detail
    except Exception as e:
        return False, f"smoke failed: {type(e).__name__}: {e}"


def selfcheck_main(argv):
    """`mobile-use selfcheck [--train]` — print the self-validation report; exit 0 iff healthy."""
    if argv and argv[0] in {"-h", "--help"}:
        print("mobile-use selfcheck — self-validation of the perception/training + action surface.\n\n"
              "USAGE:\n"
              "  mobile-use selfcheck            dep-rung matrix + action-surface + training smoke\n"
              "  mobile-use selfcheck --train    also run a bounded 1-epoch real YOLO train (needs [yolo])\n\n"
              "Exit 0 iff the action surface is consistent AND the training smoke builds a\n"
              "non-empty dataset. Missing OPTIONAL deps are reported, not failed.\n"
              "(For DEVICE connectivity, use `mobile-use --doctor`.)\n")
        return 0
    run_train = "--train" in argv

    print("mobile-use selfcheck — harness self-validation\n")

    print("Local grounding rungs (YOLO -> template -> tree -> VLM):")
    for name, ok, detail in dep_rung_matrix():
        print(f"  {_OK if ok else _NO} {name:<20} {detail}")

    print("\nAction surface (ACTION_VERBS / RECORDED_HELPERS vs real helpers):")
    issues = action_surface_issues()
    if issues:
        for msg in issues:
            print(f"  {_NO} {msg}")
    else:
        print(f"  {_OK} consistent — no phantom verbs, no duplicates")

    print("\nTraining pipeline smoke (device-free):")
    smoke_ok, smoke_detail = training_smoke(run_train=run_train)
    print(f"  {_OK if smoke_ok else _NO} {smoke_detail}")

    healthy = not issues and smoke_ok
    print(f"\n{_OK + ' healthy' if healthy else _NO + ' problems found'} — "
          f"core invariants {'hold' if healthy else 'FAILED'}.")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(selfcheck_main(sys.argv[1:]))
