"""Perception signatures + action cache — native-faster repeated screens.

Two things live here:

  * ``screen_signature`` — a stable content hash of a screen's interactable
    layout. Identical screens (same labels + quantized positions + foreground
    app) hash equal. It is the shared key for BOTH the self-labeling dataset
    (B2) and the action cache below (B3).

  * ``PerceptionCache`` — maps ``(task, step-bucket, screen_signature)`` to the
    action the LLM chose last time, so a repeated identical screen replays the
    cached action and SKIPS the LLM round-trip (the latency hotspot). A miss
    falls back to the LLM and is recorded for next time.

No third-party deps — pure stdlib, so it is always on the fast path.
"""
import hashlib
import json
import sys
import time

# One warning per distinct locator-rejection reason (no per-frame spam).
_LOCATOR_WARNED = set()


def _warn_locator_once(reason):
    """Surface, once per distinct reason, WHY a configured detector was rejected."""
    if reason not in _LOCATOR_WARNED:
        _LOCATOR_WARNED.add(reason)
        print(f"[mobile-use] perception locator: {reason}; falling back to VLM-only "
              "grounding.", file=sys.stderr)


def screen_signature(marks, app=None, quantum=8):
    """Stable 16-hex-char hash of a screen's set-of-marks + foreground app.

    Positions are quantized to ``quantum``-px buckets so sub-pixel jitter between
    otherwise-identical screens does not change the signature. Marks are sorted
    so ordering differences do not either.
    """
    norm = []
    for m in (marks or []):
        if not isinstance(m, dict):
            continue
        cx = m.get("cx") or 0
        cy = m.get("cy") or 0
        # Floor into fixed grid cells — same cell => same signature. (round() would
        # straddle cell boundaries, splitting near-identical positions.)
        norm.append((
            str(m.get("label", "")),
            str(m.get("type", "")),
            int(cx // quantum),
            int(cy // quantum),
        ))
    norm.sort()
    app_id = ""
    if isinstance(app, dict):
        app_id = app.get("bundleId") or app.get("package") or ""
    payload = json.dumps([str(app_id), norm], sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


class PerceptionCache:
    """In-memory ``(task, step, screen_sig) -> action`` memo with a TTL.

    The step is bucketed coarsely (so a screen reached at slightly different step
    counts still hits) and entries expire after ``ttl`` seconds, bounding how
    stale a replayed action can be. The cache is deliberately conservative: it
    only ever returns an EXACT signature match, and the caller always keeps the
    LLM as the fallback path, so a wrong hit can at worst cost one redundant LLM
    call on the next step.
    """

    def __init__(self, ttl=120.0, enabled=True, step_bucket=3):
        self.ttl = ttl
        self.enabled = enabled
        self.step_bucket = max(1, int(step_bucket))
        self._store = {}      # key -> (action, epoch)
        self.hits = 0
        self.misses = 0

    def _key(self, task, step, signature):
        return (str(task), step // self.step_bucket, signature)

    def get(self, task, step, signature, now=None):
        """Return a cached action for this screen, or None on miss/expiry."""
        if not self.enabled or signature is None:
            self.misses += 1
            return None
        now = time.time() if now is None else now
        entry = self._store.get(self._key(task, step, signature))
        if entry is None:
            self.misses += 1
            return None
        action, epoch = entry
        if now - epoch > self.ttl:
            self.misses += 1
            return None
        self.hits += 1
        return action

    def put(self, task, step, signature, action, now=None):
        """Record the LLM's chosen action for this screen for future replay."""
        if not self.enabled or signature is None or not isinstance(action, dict):
            return
        now = time.time() if now is None else now
        self._store[self._key(task, step, signature)] = (action, now)

    @property
    def stats(self):
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "size": len(self._store),
        }


def _run_locator(locator, image):
    """Run any grounding locator on one image -> list of matches ([] on miss/None).

    Accepts a YoloDetector (``predict``), a LocalElementMatcher (``locate_all``), or any
    object exposing one of those — so the measured benchmark is agnostic to which local
    grounding path is under test.
    """
    if locator is None:
        return []
    try:
        if hasattr(locator, "predict"):
            return locator.predict(image) or []
        if hasattr(locator, "locate_all"):
            return locator.locate_all(image) or []
    except Exception:
        return []
    return []


def measured_benchmark(images, locator=None, vlm_latency_ms=120.0):
    """REAL wall-clock benchmark of local grounding over ACTUAL screenshots.

    Unlike ``synthetic_benchmark`` (which models LLM calls), this times the local
    grounding path with ``perf_counter`` on real image files. Each image the local
    path GROUNDS (>=1 match) is one that can skip the VLM round-trip; ungrounded
    images still pay the (modeled) VLM cost. The returned dict carries BOTH:

      * a deterministic, count-based surface — ``images``, ``grounded``,
        ``llm_calls_baseline`` (= images), ``llm_calls_local`` (= images - grounded) —
        which tests assert on, and
      * measured millisecond figures (``measured_local_compute_ms``, ``per_image_ms``,
        ``local_total_ms``, ``speedup``) which are REPORTED for humans but must NEVER be
        asserted as thresholds (wall-clock is flaky under load — see the bench history).
    """
    images = list(images or [])
    n = len(images)
    per_image_ms = []
    grounded = 0
    t_all = time.perf_counter()
    for img in images:
        t0 = time.perf_counter()
        matches = _run_locator(locator, img)
        per_image_ms.append(round((time.perf_counter() - t0) * 1e3, 3))
        if matches:
            grounded += 1
    measured_local_compute_ms = round((time.perf_counter() - t_all) * 1e3, 3)

    llm_calls_baseline = n
    llm_calls_local = n - grounded
    baseline_ms = n * vlm_latency_ms                              # modeled VLM-bound
    local_total_ms = measured_local_compute_ms + llm_calls_local * vlm_latency_ms
    return {
        "mode": "measured",
        "images": n,
        "grounded": grounded,
        "llm_calls_baseline": llm_calls_baseline,
        "llm_calls_local": llm_calls_local,
        "vlm_latency_ms": vlm_latency_ms,
        "measured_local_compute_ms": measured_local_compute_ms,   # REAL (reported only)
        "per_image_ms": per_image_ms,                             # REAL (reported only)
        "baseline_ms": round(baseline_ms, 3),                     # modeled
        "local_total_ms": round(local_total_ms, 3),               # measured + modeled
        "speedup": round(baseline_ms / local_total_ms, 3) if local_total_ms else None,
    }


def synthetic_benchmark(llm_latency_ms=120.0, steps=10, repeats_same_screen=True):
    """Before/after latency of the decide loop, with vs without the action cache.

    The dominant per-step cost is the LLM round-trip, so latency is *modeled*
    deterministically as ``llm_calls * llm_latency_ms`` — NOT measured via real
    ``time.sleep`` (which is wall-clock-flaky under CPU contention and would make
    this non-reproducible). The cache's win is the reduction in LLM calls, which
    this counts exactly; the modeled ms is that count times the per-call cost.
    Mirrors run()'s anti-consecutive-replay guard. Pure + deterministic.
    """
    def _count_llm_calls(enabled):
        cache = PerceptionCache(enabled=enabled, ttl=9_999)
        llm_calls = 0
        last_sig = None
        for step in range(steps):
            sig = "screenA" if repeats_same_screen else f"screen{step}"
            cached = cache.get("task", step, sig)
            if cached is not None and sig != last_sig:
                last_sig = sig                  # replay — no LLM round-trip
            else:
                llm_calls += 1                  # the latency hotspot fires
                cache.put("task", step, sig, {"fn": "tap"})
                last_sig = None
        return llm_calls

    base_calls = _count_llm_calls(False)
    cached_calls = _count_llm_calls(True)
    base_ms = base_calls * llm_latency_ms
    cached_ms = cached_calls * llm_latency_ms
    return {
        "steps": steps,
        "llm_latency_ms": llm_latency_ms,
        "baseline_ms": round(base_ms, 2),
        "cached_ms": round(cached_ms, 2),
        "speedup": round(base_calls / cached_calls, 2) if cached_calls else None,
        "llm_calls_baseline": base_calls,
        "llm_calls_cached": cached_calls,
    }


def _gather_images(arg):
    """Resolve a dir or glob pattern to a sorted list of image paths."""
    import glob
    import os
    if os.path.isdir(arg):
        pats = [os.path.join(arg, "*.png"), os.path.join(arg, "*.jpg")]
    else:
        pats = [arg]
    out = []
    for p in pats:
        out.extend(glob.glob(p))
    return sorted(out)


def _build_locator(weights=None):
    """Best available local grounding locator (YOLO if weights+dep, else template). None.

    All optional deps imported lazily so this module stays pure-stdlib at import time.
    """
    # Prefer a trained YOLO detector when weights (or MU_DETECTOR_WEIGHTS) + dep exist.
    # When weights ARE configured but the detector is rejected, say WHY (once) so a
    # broken/incompatible checkpoint doesn't silently degrade to the VLM-only baseline.
    import os
    w = weights or os.environ.get("MU_DETECTOR_WEIGHTS")
    if w:
        try:
            from mobile_use.train_detector import YoloDetector
            from mobile_use.train_detector import available as yolo_available
            if not yolo_available():
                _warn_locator_once("weights configured but ultralytics is not installed "
                                   "(`pip install 'mobile-use[yolo]'`)")
            elif not os.path.exists(w):
                _warn_locator_once(f"configured weights path does not exist: {w}")
            else:
                det = YoloDetector(w)
                if det.available():
                    return det
                _warn_locator_once(f"configured weights {w} present but did not load")
        except Exception as e:
            _warn_locator_once(f"YOLO locator build failed ({type(e).__name__}: {e})")
    # Fall back to the template matcher built from captured/seed samples.
    try:
        from mobile_use.local_detector import LocalElementMatcher
        from mobile_use.local_detector import available as tm_available
        if tm_available():
            m = LocalElementMatcher.from_session()
            if m.template_count:
                return m
    except Exception:
        pass
    return None


_BENCH_USAGE = (
    "mobile-use bench-perception — before/after perception latency.\n\n"
    "USAGE:\n"
    "  mobile-use bench-perception                     synthetic (modeled LLM calls)\n"
    "  mobile-use bench-perception --images DIR|GLOB   REAL measured local grounding\n"
    "  mobile-use bench-perception --images DIR --weights best.pt   use a trained YOLO\n"
    "  mobile-use bench-perception --synthetic         force the modeled benchmark\n\n"
    "With --images, times the local detector/matcher (real wall-clock) over actual\n"
    "screenshots and reports how many skip the VLM round-trip. Without it, falls back\n"
    "to the deterministic modeled benchmark. Measured ms are reported, not asserted.\n"
)


def bench_main(argv):
    """`mobile-use bench-perception [--images DIR|GLOB] [--weights P] [--synthetic]`."""
    argv = list(argv or [])
    if argv and argv[0] in {"-h", "--help"}:
        print(_BENCH_USAGE)
        return 0
    images_arg = weights = None
    force_synthetic = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--images" and i + 1 < len(argv):
            images_arg = argv[i + 1]; i += 1
        elif a == "--weights" and i + 1 < len(argv):
            weights = argv[i + 1]; i += 1
        elif a == "--synthetic":
            force_synthetic = True
        i += 1

    if images_arg and not force_synthetic:
        images = _gather_images(images_arg)
        if not images:
            print(f"No images matched {images_arg!r}.")
            return 1
        locator = _build_locator(weights)
        if locator is None:
            print("No local detector available (need [yolo]+--weights or [detection]+"
                  "captured samples) — reporting VLM-only baseline.")
        r = measured_benchmark(images, locator=locator)
        print(f"Measured perception benchmark over {r['images']} images "
              f"(VLM @ {r['vlm_latency_ms']}ms/call):")
        print(f"  grounded locally   : {r['grounded']}/{r['images']} "
              f"(skip the VLM round-trip)")
        print(f"  LLM calls baseline : {r['llm_calls_baseline']}")
        print(f"  LLM calls w/ local : {r['llm_calls_local']}")
        print(f"  local compute      : {r['measured_local_compute_ms']}ms measured")
        print(f"  baseline (modeled) : {r['baseline_ms']}ms")
        print(f"  local total        : {r['local_total_ms']}ms")
        print(f"  speedup            : {r['speedup']}x")
        return 0

    r = synthetic_benchmark()
    print(f"Perception cache benchmark ({r['steps']} steps @ {r['llm_latency_ms']}ms/LLM call):")
    print(f"  baseline (no cache): {r['baseline_ms']}ms, {r['llm_calls_baseline']} LLM calls")
    print(f"  cached             : {r['cached_ms']}ms, {r['llm_calls_cached']} LLM calls")
    print(f"  speedup            : {r['speedup']}x")
    return 0
