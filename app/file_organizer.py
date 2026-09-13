"""File organizer: one clean PDF per student in homework/<assignment>/.

- PDF in -> copy/rename to '<Official Name>.pdf' (or _v2 on resubmit)
- Multiple PDFs in -> merged to single '<Official Name>.pdf' (pypdf)
- Images in -> sort by name, merge to single '<Official Name>.pdf' (Pillow)
- Links -> appended to links.txt (list-only for MVP)
- Illegal filename chars stripped; duplicates get ' (2)' suffix.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import profiles

BASE_DIR = Path(__file__).resolve().parent.parent


def homework_root() -> Path:
    profiles.ensure_profile()
    return profiles.homework_dir()

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
HEIC_EXTS = {".heic", ".heif"}  # Pillow cannot read these -> flagged unsupported
PDF_EXT = ".pdf"

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    clean = _ILLEGAL.sub("", (name or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip().rstrip(".")
    return clean or "Unnamed"


def assignment_dirs(assignment_key: str) -> dict[str, Path]:
    base = homework_root() / assignment_key
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


def merge_pdfs(pdf_paths: list[Path], dest: Path) -> Path:
    """Merge sorted PDFs into one PDF via pypdf. Raises on empty/corrupt."""
    from pypdf import PdfReader, PdfWriter

    ordered = sorted(pdf_paths, key=lambda p: p.name.lower())
    if not ordered:
        raise ValueError("no pdfs provided")
    writer = PdfWriter()
    for p in ordered:
        reader = PdfReader(str(p))
        if reader.is_encrypted:
            raise ValueError(f"encrypted pdf, open manually: {p.name}")
        for page in reader.pages:
            writer.add_page(page)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        writer.write(f)
    return dest


def normalize_pdfs(pdf_paths: list[Path], normalized_dir: Path, official_name: str) -> Path:
    if len(pdf_paths) == 1:
        return normalize_pdf(pdf_paths[0], normalized_dir, official_name)
    dest = next_unique_pdf(normalized_dir, official_name)
    return merge_pdfs(pdf_paths, dest)


def dedupe_files(paths: list[Path]) -> tuple[list[Path], int]:
    """Drop byte-identical duplicates (students often attach the same file
    twice). Returns (unique paths, duplicate count)."""
    import hashlib as _hl
    seen: dict[str, Path] = {}
    dups = 0
    for p in sorted(paths, key=lambda x: x.name.lower()):
        h = _hl.sha256(p.read_bytes()).hexdigest()
        if h in seen:
            dups += 1
        else:
            seen[h] = p
    return list(seen.values()), dups


def organize_originals(assignment_key: str, uid_to_official: dict[str, str]) -> dict:
    """Group original/ files by Classroom userId prefix and normalize per student.

    Returns {official_name: {"pdf": path|None, "images": [...], "skipped": [...],
    "dest": path|None, "error": str|None}}.
    """
    dirs = assignment_dirs(assignment_key)
    by_uid: dict[str, list[Path]] = {}
    for p in sorted(dirs["original"].iterdir()):
        if not p.is_file() or "__" not in p.name:
            continue
        uid, _ = p.name.split("__", 1)
        by_uid.setdefault(uid, []).append(p)
    report = {}
    for uid, files in by_uid.items():
        official = uid_to_official.get(uid)
        if official is None:
            report[uid] = {"dest": None, "skipped": [f.name for f in files],
                           "error": "uid not in roster (other student)"}
            continue
        pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
        imgs = [f for f in files if f.suffix.lower() in IMAGE_EXTS]
        other = [f for f in files
                 if f.suffix.lower() not in IMAGE_EXTS | {".pdf"}]
        pdfs, pdf_dups = dedupe_files(pdfs)
        imgs, img_dups = dedupe_files(imgs)
        try:
            if pdfs and not imgs:
                dest = normalize_pdfs(pdfs, dirs["normalized"], official)
            elif imgs and not pdfs:
                dest = normalize_images(imgs, dirs["normalized"], official)
            elif pdfs and imgs:
                dest = None
                raise ValueError("mixed pdf+images, merge manually")
            else:
                dest = None
            report[official] = {"dest": str(dest) if dest else None,
                                "pdf": [f.name for f in pdfs],
                                "images": [f.name for f in imgs],
                                "dups_dropped": pdf_dups + img_dups,
                                "skipped": [f.name for f in other], "error": None}
        except Exception as e:
            report[official] = {"dest": None,
                                "pdf": [f.name for f in pdfs],
                                "images": [f.name for f in imgs],
                                "skipped": [f.name for f in other], "error": str(e)[:200]}
    return report


def record_link(assignment_key: str, official_name: str, url: str) -> None:
    links = assignment_dirs(assignment_key)["base"] / "links.txt"
    with links.open("a", encoding="utf-8") as f:
        f.write(f"{sanitize(official_name)} :: {url.strip()}\n")
