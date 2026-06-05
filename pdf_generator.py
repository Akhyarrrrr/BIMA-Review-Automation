import io
import json
import re
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_BASE = Path(__file__).resolve().parent
LOGO_PATH = _BASE / "logo_bima.png"
MANIFEST_FILENAME = "bima_pdf_manifest.json"
ZIP_FILENAME = "semua_komentar_reviewer.zip"


def _manifest_row(label: str, entry: dict) -> dict:
    return {
        "label": label,
        "nidn": str(entry.get("nidn", "-")),
        "nama_ketua": str(entry.get("nama_ketua", "")),
        "pt": str(entry.get("pt", "-")),
        "email": str(entry.get("email", "-")),
        "judul_count": int(entry.get("judul_count", 0)),
        "attachment_name": str(entry.get("attachment_name", "")),
        "pdf_path": str(entry.get("pdf_path", "")),
    }


def save_pdf_manifest(output_dir, pdf_data_dict: dict) -> None:
    """Simpan daftar PDF ke JSON agar tidak bergantung pada session_state Streamlit."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    entries = [_manifest_row(lbl, e) for lbl, e in pdf_data_dict.items()]
    payload = {"version": 1, "count": len(entries), "entries": entries}
    path = root / MANIFEST_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pdf_manifest(output_dir) -> dict | None:
    """Muat mapping label → metadata + pdf_path dari manifest."""
    root = Path(output_dir)
    path = root / MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    out = {}
    for row in payload.get("entries", []):
        label = row.get("label")
        if not label:
            continue
        entry = {k: v for k, v in row.items() if k != "label"}
        out[label] = entry
    return out or None


def preserve_nidn_raw(val):
    """Ambil NIDN sebagai teks; hilangkan artefak float Excel (mis. 12345.0)."""
    if pd.isna(val) or val is None:
        return ""
    if isinstance(val, float):
        if val.is_integer():
            return str(int(val))
        s = str(val).strip()
        return s.rstrip("0").rstrip(".") if "." in s else s
    s = str(val).strip()
    if s.lower() in ("nan", "none", "-", ""):
        return ""
    if re.fullmatch(r"\d+\.0", s):
        return s[:-2]
    return s


def postprocess_review_dataframe(df):
    """Normalisasi kolom nidn + kunci grup (_nidn_key) agar pengelompokan per NIDN."""
    out = df.copy()
    if "Email" in out.columns:
        # Hindari warning Arrow Streamlit: kolom object campuran (int/str/None).
        out["Email"] = (
            out["Email"]
            .map(lambda x: "" if pd.isna(x) else str(x).strip())
            .replace({"nan": "", "None": ""})
        )
    if "nidn" not in out.columns:
        return out
    cleaned = out["nidn"].map(preserve_nidn_raw)
    out["nidn"] = cleaned
    keys = []
    for idx, n in cleaned.items():
        ns = str(n).strip() if pd.notna(n) and str(n).strip() else ""
        if not ns:
            keys.append(f"__NO_NIDN_{idx}")
        else:
            keys.append(ns)
    out["_nidn_key"] = keys
    return out


def recipient_label_from_row(row):
    nd = preserve_nidn_raw(row.get("nidn", "")) or "-"
    nama = row.get("nama_ketua", "")
    pt = row.get("PT", "")
    if pd.isna(nama):
        nama = ""
    if pd.isna(pt):
        pt = ""
    return f"{nama} — NIDN {nd} — {pt}"


def safe_pdf_basename(nidn_disp, nama_ketua, max_each=60):
    def part(x):
        s = re.sub(r"[^\w\-.]+", "_", str(x), flags=re.UNICODE).strip("_")
        return (s or "x")[:max_each]

    n = part(nidn_disp if nidn_disp and nidn_disp != "-" else "unknown_nidn")
    m = part(nama_ketua)
    return f"Komentar_Reviewer_{n}_{m}.pdf"


def clean_val(val):
    if pd.isna(val) or str(val).strip() in ["-", "", "nan", "NaN"]:
        return "-"
    return str(val).strip()


def escape_xml(text):
    """Escape XML special chars for ReportLab Paragraph"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def truncate(text, max_chars=1800):
    if len(text) > max_chars:
        return text[:max_chars] + " ...[berlanjut]"
    return text


def build_pdf_for_ketua(nama_ketua, nidn, pt, rows):
    buffer = io.BytesIO()
    PAGE = landscape(A4)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()

    s_normal = ParagraphStyle(
        "s_normal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
    )
    s_title = ParagraphStyle(
        "s_title",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    s_cell = ParagraphStyle(
        "s_cell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9.5,
        wordWrap="CJK",
    )
    s_hdr = ParagraphStyle(
        "s_hdr",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.white,
    )

    story = []

    title_para = Paragraph("KOMENTAR REVIEWER USULAN BIMA", s_title)

    if LOGO_PATH.is_file():
        logo_w = 2.0 * cm
        logo_h = 2.0 * cm
        logo = Image(str(LOGO_PATH), width=logo_w, height=logo_h)
        header = Table(
            [[logo, title_para]],
            colWidths=[logo_w + 0.4 * cm, None],
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(header)
    else:
        story.append(title_para)

    story.append(Spacer(1, 0.15 * cm))

    # Info block
    info_data = [
        [
            Paragraph("<b>Nama Ketua</b>", s_normal),
            Paragraph(f": {escape_xml(str(nama_ketua))}", s_normal),
        ],
        [
            Paragraph("<b>NIDN</b>", s_normal),
            Paragraph(f": {escape_xml(str(nidn))}", s_normal),
        ],
        [
            Paragraph("<b>Perguruan Tinggi</b>", s_normal),
            Paragraph(f": {escape_xml(str(pt))}", s_normal),
        ],
    ]
    info_tbl = Table(info_data, colWidths=[4 * cm, None])
    info_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(info_tbl)
    story.append(Spacer(1, 0.3 * cm))

    story.append(
        Paragraph(
            "Bersama ini kami sampaikan komentar reviewer terhadap protokol penelitian &amp; "
            "pengabdian yang diusulkan ke Bima:",
            s_normal,
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    col_w = [3.5 * cm, 5.5 * cm, 5.2 * cm, 5.2 * cm, 5.2 * cm]

    header_row = [
        Paragraph("Skema", s_hdr),
        Paragraph("Judul", s_hdr),
        Paragraph("Komentar Seleksi Administrasi 1", s_hdr),
        Paragraph("Komentar Evaluasi Dokumen 1", s_hdr),
        Paragraph("Komentar Evaluasi Dokumen 2", s_hdr),
    ]
    table_data = [header_row]

    for row in rows:

        def cell(key, max_c=1800):
            return Paragraph(
                escape_xml(truncate(clean_val(row.get(key, "-")), max_c)), s_cell
            )

        table_data.append(
            [
                cell("Skema", 400),
                cell("Judul", 600),
                cell("Komentar Seleksi Administrasi 1"),
                cell("Komentar Evaluasi Dokumen 1"),
                cell("Komentar Evaluasi Dokumen 2"),
            ]
        )

    tbl = LongTable(table_data, colWidths=col_w, repeatRows=1, splitByRow=True)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#EBF3FB")],
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("SPLITLAST", (0, 0), (-1, -1), 1),
            ]
        )
    )

    story.append(tbl)
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def load_pdf_entry_bytes(entry):
    """Ambil isi PDF dari entri hasil generate (disk atau bytes di memori)."""
    path = entry.get("pdf_path")
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_bytes()
    raw = entry.get("pdf")
    return raw if raw is not None else b""


def generate_all_pdfs(df, progress_callback=None, output_dir=None):
    """Satu PDF per kombinasi (NIDN, nama_ketua, PT).

    Jika ``output_dir`` diisi, PDF ditulis ke disk dan entri hanya berisi ``pdf_path``
    (disarankan untuk Streamlit agar session_state tidak membengkak).
    """
    if "_nidn_key" not in df.columns:
        df = postprocess_review_dataframe(df)

    out_root = Path(output_dir) if output_dir is not None else None
    if out_root is not None:
        out_root.mkdir(parents=True, exist_ok=True)

    result = {}
    grouped = df.groupby(["_nidn_key", "nama_ketua", "PT"], dropna=False, sort=False)
    total = grouped.ngroups
    used_filenames = set()

    for i, (_keys, group) in enumerate(grouped, start=1):
        first = group.iloc[0]
        nama_ketua = first.get("nama_ketua", "")
        nidn_disp = preserve_nidn_raw(first.get("nidn", "")) or "-"
        pt = clean_val(first.get("PT", "-"))
        rows = group.to_dict("records")
        pdf_bytes = build_pdf_for_ketua(nama_ketua, nidn_disp, pt, rows)
        label = recipient_label_from_row(first)
        fname = safe_pdf_basename(nidn_disp, nama_ketua)
        stem = Path(fname).stem
        unique_fname = fname
        n_try = 0
        while unique_fname in used_filenames:
            n_try += 1
            unique_fname = f"{stem}_{n_try}.pdf"
        used_filenames.add(unique_fname)

        entry = {
            "nidn": nidn_disp,
            "nama_ketua": nama_ketua,
            "pt": pt,
            "email": clean_val(first.get("Email", "-")),
            "judul_count": len(rows),
            "attachment_name": unique_fname,
        }
        if out_root is not None:
            dest = out_root / unique_fname
            dest.write_bytes(pdf_bytes)
            entry["pdf_path"] = str(dest.resolve())
        else:
            entry["pdf"] = pdf_bytes

        result[label] = entry
        if progress_callback:
            progress_callback(i, total, nama_ketua)
    return result
