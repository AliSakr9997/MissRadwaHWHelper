"""File organizer: one clean PDF per student in homework/<assignment>/.

- PDF in -> copy/rename to '<Official Name>.pdf' (or _v2 on resubmit)
- Images in -> sort by name, merge to single '<Official Name>.pdf' (Pillow)
- Links -> appended to links.txt (list-only for MVP)
- Illegal filename chars stripped; duplicates get ' (2)' suffix.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HOMEWORK_DIR = BASE_DIR / "homework"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
HEIC_EXTS = {".heic", ".heif"}  # Pillow cannot read these -> flagged unsupported
PDF_EXT = ".pdf"

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    clean = _ILLEGAL.sub("", (name or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip().rstrip(".")
    return clean or "Unnamed"


def assignment_dirs(assignment_key: str) -> dict[str, Path]:
    base = HOMEWORK_DIR / assignment_key
    paths = {
        "base": base,
        "original": base / "original",
        "normalized": base / "normalized",
        "marked": base / "marked",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    for stub in ("reports.json", "missing.json"):
        f = base / stub
        if not f.exists():
            f.write_text("[]", encoding="utf-8")
    links = base / "links.txt"
    if not links.exists():
        links.write_text("# link-only submissions, one URL per line\n", encoding="utf-8")
    return paths


def next_unique_pdf(normalized_dir: Path, official_name: str) -> Path:
    safe = sanitize(official_name)
    candidate = normalized_dir / f"{safe}.pdf"
    if not candidate.exists():
        return candidate
    v = 2
    while True:
        candidate = normalized_dir / f"{safe}_v{v}.pdf"
        if not candidate.exists():
            return candidate
        v += 1


def normalize_pdf(src: Path, normalized_dir: Path, official_name: str) -> Path:
    dest = next_unique_pdf(normalized_dir, official_name)
    shutil.copy2(src, dest)
    return dest


def images_to_pdf(image_paths: list[Path], dest: Path) -> Path:
    """Merge sorted images into one PDF via Pillow. Raises on HEIC/empty."""
    from PIL import Image

    ordered = sorted(image_paths, key=lambda p: p.name.lower())
    if not ordered:
        raise ValueError("no images provided")
    bad = [p for p in ordered if p.suffix.lower() in HEIC_EXTS]
    if bad:
        raise ValueError(f"HEIC not supported, convert manually: {[p.name for p in bad]}")
    imgs = []
    for p in ordered:
        im = Image.open(p)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
            im = bg
        else:
            im = im.convert("RGB")
        imgs.append(im)
    first, rest = imgs[0], imgs[1:]
    dest.parent.mkdir(parents=True, exist_ok=True)
    first.save(dest, save_all=bool(rest), append_images=rest)
    for im in imgs:
        im.close()
    return dest


def normalize_images(image_paths: list[Path], normalized_dir: Path, official_name: str) -> Path:
    dest = next_unique_pdf(normalized_dir, official_name)
    return images_to_pdf(image_paths, dest)


def record_link(assignment_key: str, official_name: str, url: str) -> None:
    links = assignment_dirs(assignment_key)["base"] / "links.txt"
    with links.open("a", encoding="utf-8") as f:
        f.write(f"{sanitize(official_name)} :: {url.strip()}\n")
