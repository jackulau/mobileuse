"""Synthetic seed UI dataset — a device-free, dependency-light source of labelled
detection samples.

The self-labeling capture (B2) only produces samples while a real device is driven.
To exercise (and prove) the whole ``dataset -> train -> weights -> ground`` pipeline
without a device, this module RENDERS labelled UI screens with Pillow (already a hard
dependency) and emits rows in the exact shape ``collector.load_detection_samples``
returns — ``{screenshot, bbox, label, crop, ...}`` — so ``train_detector.build_yolo_dataset``
and ``local_detector.LocalElementMatcher.from_samples`` both consume them unchanged.

Design choice that makes the data LEARNABLE: every label has a FIXED visual identity
(background colour + glyph), so "Search" looks the same across screens while its
position varies. A detector can then learn label->appearance from a handful of images;
positions vary so it cannot cheat on location. Fully deterministic given ``seed``.
"""
import json
import os
import random
from pathlib import Path

# Each label maps to a stable RGB fill — distinct, high-contrast, so a tiny model
# (or template matcher) separates them easily. Ordering is fixed for determinism.
_PALETTE = {
    "Search":   (33, 118, 220),
    "Send":     (32, 170, 90),
    "Back":     (220, 96, 40),
    "Home":     (150, 70, 200),
    "Settings": (90, 90, 95),
    "Cart":     (210, 160, 30),
}
DEFAULT_LABELS = ("Search", "Send", "Back")


def _text_size(draw, text, font):
    """(w, h) of ``text`` across Pillow versions (textbbox new, textsize old)."""
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    except Exception:                      # very old Pillow
        return draw.textsize(text, font=font)


def generate_seed_dataset(out_dir, n=8, seed=0, labels=DEFAULT_LABELS,
                          size=(320, 640), per_screen=3, save_crop=True):
    """Render ``n`` synthetic UI screens and return detection samples.

    Each sample is ``{screenshot, bbox:[x,y,w,h] (pixels), label, crop, screen_sig,
    source:"synthetic"}`` — the canonical training-row shape. Also writes a
    ``seed_detections.jsonl`` next to the images so the layout mirrors the collector.
    Deterministic: same ``seed`` -> identical pixels, boxes, and ordering.
    """
    from PIL import Image, ImageDraw, ImageFont

    out = Path(out_dir)
    shots_dir, crops_dir = out / "screenshots", out / "crops"
    shots_dir.mkdir(parents=True, exist_ok=True)
    if save_crop:
        crops_dir.mkdir(parents=True, exist_ok=True)

    labels = [lbl for lbl in labels if lbl in _PALETTE] or list(DEFAULT_LABELS)
    rng = random.Random(seed)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    W, H = size
    # Element box size (pixels), clamped to fit the canvas so boxes stay in-frame even
    # on a tiny canvas. No-op at the default size (96<=W-16, 40<=H-16).
    bw, bh = min(96, max(8, W - 16)), min(40, max(8, H - 16))
    samples = []
    jsonl_lines = []
    for i in range(n):
        img = Image.new("RGB", (W, H), (245, 246, 248))
        draw = ImageDraw.Draw(img)
        stem = f"seed_{seed}_{i:03d}"
        shot_path = shots_dir / f"{stem}.png"

        # Choose labels for this screen (cycle so every label appears across the set),
        # then lay them out on a jittered vertical stack — positions vary per screen.
        placed = []
        for j in range(per_screen):
            label = labels[(i + j) % len(labels)]
            x = rng.randint(8, max(8, W - bw - 8))
            y = 8 + j * (bh + 24) + rng.randint(0, 12)
            if y + bh > H - 8:
                break
            color = _PALETTE[label]
            draw.rounded_rectangle([x, y, x + bw, y + bh], radius=8, fill=color)
            # A per-label glyph (first char) + the label text, in white for contrast.
            if font is not None:
                tw, th = _text_size(draw, label, font)
                draw.text((x + (bw - tw) / 2, y + (bh - th) / 2), label,
                          fill=(255, 255, 255), font=font)
            bbox = [float(x), float(y), float(bw), float(bh)]
            placed.append((label, bbox))

        if not placed:
            # Guarantee >=1 in-frame box per screen: a small canvas + large per_screen
            # could otherwise emit a screen with zero labels (silent empty sample).
            # Clamp a single box to fit even a tiny canvas.
            fbw, fbh = min(bw, max(8, W - 16)), min(bh, max(8, H - 16))
            x, y = 8, 8
            label = labels[i % len(labels)]
            draw.rounded_rectangle([x, y, x + fbw, y + fbh], radius=8, fill=_PALETTE[label])
            if font is not None:
                tw, th = _text_size(draw, label, font)
                draw.text((x + (fbw - tw) / 2, y + (fbh - th) / 2), label,
                          fill=(255, 255, 255), font=font)
            placed.append((label, [float(x), float(y), float(fbw), float(fbh)]))

        img.save(shot_path)

        for label, bbox in placed:
            crop_path = None
            if save_crop:
                x, y, w, h = (int(v) for v in bbox)
                crop_path = crops_dir / f"{stem}-{label}-{x}-{y}.png"
                img.crop((x, y, x + w, y + h)).save(crop_path)
            row = {
                "screenshot": str(shot_path),
                "bbox": bbox,
                "bbox_logical": bbox,
                "label": label,
                "crop": str(crop_path) if crop_path else None,
                "screen_sig": stem,
                "source": "synthetic",
            }
            samples.append(row)
            jsonl_lines.append(json.dumps(row))

    (out / "seed_detections.jsonl").write_text(
        "\n".join(jsonl_lines) + ("\n" if jsonl_lines else ""), encoding="utf-8")
    return samples


def main(argv=None):
    """`python -m mobile_use.synthetic_ui [out_dir] [--n N] [--seed S]` — quick generator."""
    argv = list(argv if argv is not None else os.sys.argv[1:])
    out = "synthetic-ui-dataset"
    n, seed = 8, 0
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--n" and i + 1 < len(argv):
            n = int(argv[i + 1]); i += 1
        elif a == "--seed" and i + 1 < len(argv):
            seed = int(argv[i + 1]); i += 1
        elif not a.startswith("-"):
            out = a
        i += 1
    s = generate_seed_dataset(out, n=n, seed=seed)
    print(f"Generated {len(s)} samples across {n} screens -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
