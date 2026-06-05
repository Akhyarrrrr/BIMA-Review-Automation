import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from pdf_generator import (
    MANIFEST_FILENAME,
    ZIP_FILENAME,
    build_pdf_for_ketua,
    clean_val,
    generate_all_pdfs,
    load_pdf_entry_bytes,
    load_pdf_manifest,
    postprocess_review_dataframe,
    preserve_nidn_raw,
    recipient_label_from_row,
    safe_pdf_basename,
    save_pdf_manifest,
)
from email_sender import send_all_emails, send_email, is_valid_email


def _remove_pdf_cache_dir():
    """Hapus folder PDF sementara di disk (bukan isi session_state berat)."""
    d = st.session_state.get("pdf_cache_dir")
    if d and Path(d).is_dir():
        shutil.rmtree(d, ignore_errors=True)
    st.session_state.pdf_cache_dir = None
    st.session_state.pdf_last_success = None


def _pdf_job_ready():
    c = st.session_state.get("pdf_cache_dir")
    return bool(c) and (Path(c) / MANIFEST_FILENAME).is_file()


def _get_pdf_data():
    c = st.session_state.get("pdf_cache_dir")
    if not c or not Path(c).is_dir():
        return None
    return load_pdf_manifest(c)


st.set_page_config(
    page_title="BIMA Review Automation",
    page_icon="📋",
    layout="wide",
)

# ── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1F4E79 0%, #2E86AB 100%);
        padding: 24px 32px;
        border-radius: 12px;
        margin-bottom: 24px;
        color: white;
    }
    .main-header h1 { color: white; margin: 0; font-size: 1.8rem; }
    .main-header p  { color: #d0e8f9; margin: 6px 0 0; font-size: 0.95rem; }
    .stat-card {
        background: white;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 16px 20px;
        text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,.06);
    }
    .stat-card .num  { font-size: 2rem; font-weight: 700; color: #1F4E79; }
    .stat-card .lbl  { font-size: 0.82rem; color: #666; margin-top: 4px; }
    .success-chip { background:#d4edda; color:#155724; padding:3px 10px;
                    border-radius:20px; font-size:0.82rem; font-weight:600; }
    .warn-chip    { background:#fff3cd; color:#856404; padding:3px 10px;
                    border-radius:20px; font-size:0.82rem; font-weight:600; }
    .err-chip     { background:#f8d7da; color:#721c24; padding:3px 10px;
                    border-radius:20px; font-size:0.82rem; font-weight:600; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>📋 BIMA Review Automation</h1>
    <p>Sistem otomasi pembuatan & pengiriman surat komentar reviewer ke masing-masing Ketua Peneliti</p>
</div>
""", unsafe_allow_html=True)


@st.fragment
def _fragment_batch_downloads():
    """Hanya bagian ini yang rerun saat unduh/ZIP — tidak menghapus hasil generate."""
    pdf_data = _get_pdf_data()
    if not pdf_data:
        st.warning(
            "Belum ada PDF di cache. Gunakan **Generate Semua PDF** di atas terlebih dahulu."
        )
        return

    st.markdown("#### ⬇️ Unduh hasil batch")
    st.caption(
        f"**{len(pdf_data):,}** file — diambil dari **manifest di disk** "
        "(aman untuk banyak file; tidak perlu generate ulang setelah unduh)."
    )

    col_dl1, col_dl2 = st.columns(2)
    cache = st.session_state.get("pdf_cache_dir")
    zip_path = Path(cache) / ZIP_FILENAME if cache else None

    with col_dl1:
        if st.button("📦 Buat ZIP semua PDF", key="bima_btn_build_zip"):
            if not cache or not Path(cache).is_dir():
                st.error("Folder cache hilang. Silakan **Generate Semua PDF** lagi.")
            else:
                with st.spinner("Menggabungkan ke ZIP..."):
                    with zipfile.ZipFile(
                        zip_path, "w", zipfile.ZIP_DEFLATED
                    ) as zf:
                        for _label, data in pdf_data.items():
                            fname = data.get("attachment_name") or (
                                f"Komentar_{_label[:40]}.pdf"
                            )
                            pth = data.get("pdf_path")
                            if pth and Path(pth).is_file():
                                zf.write(Path(pth), arcname=fname)
                st.success("ZIP siap — gunakan tombol unduh di bawah ini.")

        if zip_path and zip_path.is_file():
            st.download_button(
                label="⬇️ Download ZIP semua PDF",
                data=zip_path.read_bytes(),
                file_name=ZIP_FILENAME,
                mime="application/zip",
                key="bima_dl_zip_bytes",
            )

    with col_dl2:
        labels = sorted(pdf_data.keys())
        pick = st.selectbox(
            "Unduh satu PDF:",
            labels,
            key="bima_select_one_pdf",
        )
        row = pdf_data[pick]
        fn = row.get("attachment_name") or "komentar_reviewer.pdf"
        blob = load_pdf_entry_bytes(row)
        lbl = pick if len(pick) <= 70 else pick[:70] + "…"
        st.download_button(
            label=f"⬇️ Unduh PDF — {lbl}",
            data=blob if blob else b"",
            file_name=fn,
            mime="application/pdf",
            key="bima_dl_one_pdf",
            disabled=not blob,
        )


@st.fragment
def _fragment_email_send():
    """Tombol kirim email diisolasi agar tidak memicu rerun seluruh halaman."""
    pdf_data = _get_pdf_data()
    if not pdf_data:
        return

    n_ok = sum(
        1 for d in pdf_data.values() if is_valid_email(d.get("email", ""))
    )
    if n_ok == 0:
        return

    host = st.session_state.get("bima_smtp_host", "smtp.gmail.com")
    port = int(st.session_state.get("bima_smtp_port", 587) or 587)
    use_tls = bool(st.session_state.get("bima_smtp_tls", True))
    require_auth = bool(st.session_state.get("bima_smtp_require_auth", True))
    user = st.session_state.get("bima_smtp_user", "")
    password = st.session_state.get("bima_smtp_password", "")
    sender_name = st.session_state.get("bima_sender_name", "Tim Reviewer BIMA")
    body_extra = st.session_state.get("bima_email_extra_note", "") or ""

    col_send, col_test = st.columns([2, 1])

    with col_test:
        test_to = st.text_input(
            "Email untuk uji coba",
            placeholder="nama@domain.com",
            key="bima_test_email_to",
        )
        if st.button("🧪 Kirim test", key="bima_btn_test_email"):
            if require_auth and (not user or not password):
                st.error("Karena AUTH aktif, isi email pengirim dan password SMTP.")
            elif not test_to:
                st.error("Isi alamat email test.")
            else:
                first_label = sorted(pdf_data.keys())[0]
                first_data = pdf_data[first_label]
                first_nama = first_data.get("nama_ketua", first_label)
                test_pdf = load_pdf_entry_bytes(first_data)
                if not test_pdf:
                    st.error("Berkas PDF tidak ditemukan di cache.")
                else:
                    ok, msg = send_email(
                        host,
                        port,
                        user,
                        password,
                        use_tls,
                        test_to,
                        first_nama,
                        first_data.get("pt", ""),
                        test_pdf,
                        f"TEST_Komentar_{first_nama}.pdf",
                        sender_name,
                        body_extra=body_extra,
                        require_auth=require_auth,
                    )
                    if ok:
                        st.success(f"✅ Test terkirim ke {test_to}")
                    else:
                        st.error(f"❌ {msg}")

    with col_send:
        if st.button(
            "📨 Kirim semua email",
            type="primary",
            key="bima_btn_send_all",
        ):
            if require_auth and (not user or not password):
                st.error("Karena AUTH aktif, isi email pengirim dan password SMTP.")
            else:
                smtp_config = {
                    "host": host,
                    "port": port,
                    "user": user,
                    "password": password,
                    "use_tls": use_tls,
                    "require_auth": require_auth,
                    "sender_name": sender_name,
                }
                prog = st.progress(0, text="Mengirim…")
                status_ph = st.empty()
                total_e = len(pdf_data)

                def prog_cb(cur, tot, nama, status):
                    if tot:
                        prog.progress(cur / tot, text=f"[{cur}/{tot}] {nama}")
                    status_ph.caption(f"**{status}** — {nama}")

                results = send_all_emails(
                    smtp_config,
                    pdf_data,
                    progress_callback=prog_cb,
                    body_extra=body_extra,
                )
                st.session_state.email_results = results
                prog.progress(1.0, text="Selesai")
                st.rerun()


# ── Session state ────────────────────────────────────────────────────────────
if "df" not in st.session_state:
    st.session_state.df = None
if "email_results" not in st.session_state:
    st.session_state.email_results = None
if "pdf_cache_dir" not in st.session_state:
    st.session_state.pdf_cache_dir = None
if "pdf_last_success" not in st.session_state:
    st.session_state.pdf_last_success = None
if "uploaded_file_token" not in st.session_state:
    st.session_state.uploaded_file_token = None

# Default widget email (hanya jika belum ada di session)
if "bima_smtp_host" not in st.session_state:
    st.session_state.bima_smtp_host = "smtp.gmail.com"
if "bima_smtp_port" not in st.session_state:
    st.session_state.bima_smtp_port = 587
if "bima_smtp_tls" not in st.session_state:
    st.session_state.bima_smtp_tls = True
if "bima_smtp_require_auth" not in st.session_state:
    st.session_state.bima_smtp_require_auth = True
if "bima_sender_name" not in st.session_state:
    st.session_state.bima_sender_name = "Tim Reviewer BIMA"

# ═══════════════════════════════════════════════════════════════════════════
# TAB LAYOUT
# ═══════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs([
    "📤 Upload Data",
    "📊 Preview Data",
    "📄 Generate PDF",
    "✉️ Kirim Email",
])

# ══════════════════════════════════════════════════
# TAB 1 — Upload Data
# ══════════════════════════════════════════════════
with tab1:
    st.subheader("Upload File Excel Hasil Review BIMA")

    col_up, col_info = st.columns([3, 2])
    with col_up:
        uploaded = st.file_uploader(
            "Pilih file .xlsx",
            type=['xlsx'],
            help="File harus memiliki kolom: nama_ketua, nidn, PT, Skema, Judul, "
                 "Komentar Seleksi Administrasi 1, Komentar Evaluasi Dokumen 1, "
                 "Komentar Evaluasi Dokumen 2"
        )

    with col_info:
        st.info("""
**Kolom yang dibutuhkan:**
- `nama_ketua` — Nama Ketua Peneliti
- `nidn` — NIDN Ketua
- `PT` — Perguruan Tinggi
- `Email` — Email Ketua *(opsional, untuk kirim email)*
- `Skema` — Skema Penelitian
- `Judul` — Judul Proposal
- `Komentar Seleksi Administrasi 1`
- `Komentar Evaluasi Dokumen 1`
- `Komentar Evaluasi Dokumen 2`
        """)

    if uploaded:
        file_token = f"{uploaded.name}|{uploaded.size}"
        is_new_upload = file_token != st.session_state.uploaded_file_token

        if is_new_upload:
            try:
                # Baca nidn sebagai teks bila memungkinkan agar angka depan 0 di Excel tidak hilang
                try:
                    df = pd.read_excel(
                        uploaded, engine="openpyxl", dtype={"nidn": "string"}
                    )
                except Exception:
                    uploaded.seek(0)
                    df = pd.read_excel(uploaded, engine="openpyxl")
                required = ['nama_ketua', 'nidn', 'PT', 'Skema', 'Judul',
                            'Komentar Seleksi Administrasi 1',
                            'Komentar Evaluasi Dokumen 1',
                            'Komentar Evaluasi Dokumen 2']
                missing = [c for c in required if c not in df.columns]
                if missing:
                    st.error(f"❌ Kolom berikut tidak ditemukan: **{', '.join(missing)}**")
                else:
                    df = postprocess_review_dataframe(df)
                    st.session_state.df = df
                    st.session_state.uploaded_file_token = file_token
                    _remove_pdf_cache_dir()
                    st.session_state.email_results = None
                    st.success(f"✅ File berhasil dimuat! **{len(df):,}** baris data.")
            except Exception as e:
                st.error(f"❌ Gagal membaca file: {e}")

        if st.session_state.df is not None:
            df = st.session_state.df
            grp = df.groupby(["_nidn_key", "nama_ketua", "PT"], dropna=False)
            n_ketua = grp.ngroups
            n_email = df["Email"].notna().sum() if "Email" in df.columns else 0
            n_multi = (grp.size() > 1).sum()

            c1, c2, c3, c4 = st.columns(4)
            for col, num, lbl in [
                (c1, len(df), "Total Baris"),
                (c2, n_ketua, "Ketua Unik"),
                (c3, n_multi, "Ketua Multi-Judul"),
                (c4, n_email, "Email Tersedia"),
            ]:
                col.markdown(f"""
                <div class="stat-card">
                    <div class="num">{num:,}</div>
                    <div class="lbl">{lbl}</div>
                </div>""", unsafe_allow_html=True)

    elif st.session_state.df is not None:
        st.success("✅ Data sudah dimuat sebelumnya. Lihat tab **Preview Data**.")

# ══════════════════════════════════════════════════
# TAB 2 — Preview Data
# ══════════════════════════════════════════════════
with tab2:
    if st.session_state.df is None:
        st.info("⬆️ Upload file terlebih dahulu di tab **Upload Data**.")
    else:
        df = st.session_state.df

        st.subheader("Preview Seluruh Data")

        # Filters
        fc1, fc2 = st.columns([2, 3])
        with fc1:
            pt_list = sorted(df['PT'].dropna().unique().tolist())
            pt_filter = st.multiselect("Filter Perguruan Tinggi", pt_list)
        with fc2:
            ketua_search = st.text_input("Cari Nama Ketua", placeholder="Ketik sebagian nama...")

        filtered = df.copy()
        if pt_filter:
            filtered = filtered[filtered['PT'].isin(pt_filter)]
        if ketua_search:
            filtered = filtered[filtered['nama_ketua'].str.contains(
                ketua_search, case=False, na=False)]

        st.caption(f"Menampilkan **{len(filtered):,}** dari **{len(df):,}** baris")
        st.dataframe(
            filtered[[
                'nama_ketua', 'nidn', 'PT', 'Skema', 'Judul',
                'Komentar Seleksi Administrasi 1',
                'Komentar Evaluasi Dokumen 1',
                'Komentar Evaluasi Dokumen 2',
            ]],
            use_container_width=True,
            height=500,
        )

        st.markdown("---")
        st.subheader("Ringkasan per Ketua")
        summary = (
            df.groupby(["_nidn_key", "nama_ketua", "nidn", "PT"], dropna=False)
            .agg(jumlah_judul=("Judul", "count"))
            .reset_index()
            .sort_values("jumlah_judul", ascending=False)
        )
        if "Email" in df.columns:
            email_map = (
                df.groupby(["_nidn_key", "nama_ketua", "PT"], dropna=False)["Email"]
                .first()
                .reset_index()
            )
            summary = summary.merge(
                email_map, on=["_nidn_key", "nama_ketua", "PT"], how="left"
            )

        show_sum = summary.drop(columns=["_nidn_key"], errors="ignore")
        st.dataframe(show_sum, use_container_width=True, height=400)

# ══════════════════════════════════════════════════
# TAB 3 — Generate PDF
# ══════════════════════════════════════════════════
with tab3:
    if st.session_state.df is None:
        st.info("⬆️ Upload file terlebih dahulu di tab **Upload Data**.")
    else:
        df = st.session_state.df
        st.subheader("Generate PDF Surat Komentar Reviewer")

        # Single ketua preview
        st.markdown("#### 🔍 Preview PDF per penerima (NIDN + nama + PT)")
        uniq = df.drop_duplicates(["_nidn_key", "nama_ketua", "PT"], keep="first")
        label_list = [recipient_label_from_row(r) for _, r in uniq.iterrows()]
        label_list = sorted(label_list)
        selected_label = st.selectbox("Pilih baris (ketua / NIDN / PT):", label_list)

        if selected_label:
            row0 = uniq[
                uniq.apply(recipient_label_from_row, axis=1) == selected_label
            ].iloc[0]
            subset = df[
                (df["_nidn_key"] == row0["_nidn_key"])
                & (df["nama_ketua"] == row0["nama_ketua"])
                & (df["PT"] == row0["PT"])
            ]
            first = subset.iloc[0]
            nidn_disp = preserve_nidn_raw(first.get("nidn", "")) or "-"
            st.markdown(f"""
            | Field | Nilai |
            |---|---|
            | **Nama Ketua** | {first['nama_ketua']} |
            | **NIDN** | {nidn_disp} |
            | **PT** | {clean_val(first.get('PT', '-'))} |
            | **Jumlah Judul** | {len(subset)} |
            """)

            col_prev, _ = st.columns([2, 4])
            with col_prev:
                if st.button("📄 Generate PDF Preview"):
                    with st.spinner("Membuat PDF..."):
                        rows = subset.to_dict("records")
                        pt = clean_val(first.get("PT", "-"))
                        pdf_bytes = build_pdf_for_ketua(
                            first["nama_ketua"], nidn_disp, pt, rows
                        )
                    fname = safe_pdf_basename(nidn_disp, first["nama_ketua"])
                    st.download_button(
                        label="⬇️ Download PDF Preview",
                        data=pdf_bytes,
                        file_name=f"Preview_{fname}",
                        mime="application/pdf",
                    )
                    st.success("✅ PDF berhasil dibuat!")

        st.markdown("---")
        st.markdown("#### 🚀 Generate Semua PDF")
        n_pdf = df.groupby(["_nidn_key", "nama_ketua", "PT"], dropna=False).ngroups
        st.info(
            f"Akan dibuat **{n_pdf:,} file PDF** — satu per kombinasi **NIDN + nama ketua + PT** "
            "(nama sama dengan NIDN berbeda tidak digabung)."
        )

        if st.button("⚡ Generate Semua PDF", type="primary"):
            progress = st.progress(0, text="Memulai proses...")
            status_text = st.empty()
            total_k = df.groupby(
                ["_nidn_key", "nama_ketua", "PT"], dropna=False
            ).ngroups

            def _on_prog(cur, tot, nama):
                if tot:
                    progress.progress(cur / tot, text=f"[{cur}/{tot}] {nama}")
                    status_text.caption(f"Memproses: **{nama}**")

            _remove_pdf_cache_dir()
            cache_dir = tempfile.mkdtemp(prefix="bima_reviewer_")
            st.session_state.pdf_cache_dir = cache_dir

            with st.spinner("Generating PDFs..."):
                start = time.time()
                pdf_data = generate_all_pdfs(
                    df,
                    progress_callback=_on_prog,
                    output_dir=cache_dir,
                )
                elapsed = time.time() - start

            save_pdf_manifest(cache_dir, pdf_data)
            st.session_state.pdf_last_success = (
                f"**{total_k:,} PDF** selesai ({elapsed:.1f} dtk). "
                "Unduh ZIP atau per file di bawah — **tetap tersedia** setelah Anda mengklik apa pun."
            )
            progress.progress(1.0, text="✅ Selesai!")
            status_text.success("Generate selesai. Lihat bagian **Unduh hasil batch**.")

        if st.session_state.get("pdf_last_success") and _pdf_job_ready():
            st.success(st.session_state.pdf_last_success)

        if _pdf_job_ready():
            st.markdown("---")
            _fragment_batch_downloads()

# ══════════════════════════════════════════════════
# TAB 4 — Kirim Email
# ══════════════════════════════════════════════════
with tab4:
    if st.session_state.df is None:
        st.info("⬆️ Upload file terlebih dahulu di tab **Upload Data**.")
    elif not _pdf_job_ready():
        st.warning(
            "⚠️ Selesaikan **Generate Semua PDF** di tab **Generate PDF** terlebih dahulu."
        )
    else:
        pdf_data = _get_pdf_data() or {}

        st.subheader("Kirim Email ke Ketua Peneliti")

        n_with_email = sum(
            1 for d in pdf_data.values() if is_valid_email(d.get("email", ""))
        )
        n_no_email = len(pdf_data) - n_with_email

        col_e1, col_e2, col_e3 = st.columns(3)
        col_e1.markdown(
            f"""<div class="stat-card">
            <div class="num">{len(pdf_data):,}</div>
            <div class="lbl">Total PDF Siap Kirim</div>
        </div>""",
            unsafe_allow_html=True,
        )
        col_e2.markdown(
            f"""<div class="stat-card">
            <div class="num" style="color:#28a745">{n_with_email:,}</div>
            <div class="lbl">Memiliki Email</div>
        </div>""",
            unsafe_allow_html=True,
        )
        col_e3.markdown(
            f"""<div class="stat-card">
            <div class="num" style="color:#dc3545">{n_no_email:,}</div>
            <div class="lbl">Tanpa Email</div>
        </div>""",
            unsafe_allow_html=True,
        )

        if n_with_email == 0:
            st.warning(
                "⚠️ Belum ada email valid di data. "
                "Tambahkan kolom **Email** pada Excel lalu upload ulang."
            )

        st.markdown("---")
        st.markdown("#### ⚙️ Konfigurasi SMTP")

        with st.expander("📧 Pengaturan Server Email", expanded=True):
            scol1, scol2 = st.columns(2)
            with scol1:
                st.text_input(
                    "SMTP Host",
                    placeholder="smtp.gmail.com",
                    key="bima_smtp_host",
                )
                st.number_input(
                    "SMTP Port",
                    min_value=1,
                    max_value=65535,
                    key="bima_smtp_port",
                )
                st.checkbox(
                    "Gunakan STARTTLS",
                    key="bima_smtp_tls",
                )
                st.checkbox(
                    "Gunakan login SMTP (AUTH)",
                    help="Matikan jika server kampus tidak mendukung AUTH/login.",
                    key="bima_smtp_require_auth",
                )
            with scol2:
                st.text_input(
                    "Email Pengirim",
                    placeholder="youremail@gmail.com",
                    key="bima_smtp_user",
                )
                st.text_input(
                    "Password / App Password",
                    type="password",
                    help="Untuk Gmail gunakan App Password.",
                    key="bima_smtp_password",
                )
                st.text_input(
                    "Nama Pengirim",
                    key="bima_sender_name",
                )

        st.markdown("---")
        st.markdown("#### ✉️ Pesan tambahan (opsional)")
        st.text_area(
            "Teks ini disisipkan di badan email setelah salam & nama PT, sebelum paragraf standar.",
            height=120,
            placeholder="Contoh: Mohon konfirmasi penerimaan paling lambat …",
            key="bima_email_extra_note",
        )

        st.markdown("---")

        if n_with_email > 0:
            st.info(
                f"Siap mengirim ke **{n_with_email}** penerima dengan email valid "
                "(yang tanpa email akan dilewati otomatis)."
            )
            _fragment_email_send()
        else:
            st.warning("Tambahkan kolom **Email** pada data untuk menggunakan fitur ini.")

        # Results
        if st.session_state.email_results:
            st.markdown("---")
            st.subheader("📊 Hasil Pengiriman Email")
            res_df = pd.DataFrame(st.session_state.email_results)

            ok_count   = (res_df['status'] == 'ok').sum()
            skip_count = (res_df['status'] == 'skip').sum()
            err_count  = (res_df['status'] == 'error').sum()

            r1, r2, r3 = st.columns(3)
            r1.markdown(f'<div class="stat-card"><div class="num" style="color:#28a745">'
                        f'{ok_count}</div><div class="lbl">Berhasil Terkirim</div></div>',
                        unsafe_allow_html=True)
            r2.markdown(f'<div class="stat-card"><div class="num" style="color:#6c757d">'
                        f'{skip_count}</div><div class="lbl">Dilewati (No Email)</div></div>',
                        unsafe_allow_html=True)
            r3.markdown(f'<div class="stat-card"><div class="num" style="color:#dc3545">'
                        f'{err_count}</div><div class="lbl">Gagal</div></div>',
                        unsafe_allow_html=True)

            st.dataframe(res_df, use_container_width=True)

            # Download hasil
            csv = res_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Download Laporan Pengiriman (CSV)",
                data=csv,
                file_name="laporan_pengiriman_email.csv",
                mime="text/csv",
            )