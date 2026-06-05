# BIMA Review Automation

Aplikasi Streamlit untuk mengolah file Excel hasil review BIMA, membuat PDF komentar reviewer per Ketua Peneliti, dan mengirimkan PDF tersebut melalui email.

## Fitur

- Upload file Excel hasil review BIMA.
- Validasi kolom wajib pada file Excel.
- Preview data dan ringkasan per Ketua Peneliti.
- Generate PDF komentar reviewer per kombinasi NIDN, nama ketua, dan perguruan tinggi.
- Download satu PDF atau ZIP berisi semua PDF.
- Kirim email massal dengan lampiran PDF melalui SMTP.
- Download laporan hasil pengiriman email dalam format CSV dari aplikasi.

## Struktur Project

```text
.
|-- app.py              # Aplikasi utama Streamlit
|-- email_sender.py     # Utilitas validasi dan pengiriman email
|-- pdf_generator.py    # Utilitas pembuatan PDF dan manifest
|-- logo_bima.png       # Logo yang digunakan di PDF
`-- README.md
```

## Prasyarat

- Python 3.10 atau lebih baru
- pip
- Akun/server SMTP jika ingin memakai fitur kirim email

## Instalasi

Buat virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependency:

```powershell
pip install streamlit pandas openpyxl reportlab
```

## Menjalankan Aplikasi

```powershell
streamlit run app.py
```

Setelah berjalan, buka URL lokal yang ditampilkan oleh Streamlit, biasanya:

```text
http://localhost:8501
```

## Format File Excel

File Excel yang diupload harus berupa `.xlsx` dan memiliki kolom berikut:

```text
nama_ketua
nidn
PT
Skema
Judul
Komentar Seleksi Administrasi 1
Komentar Evaluasi Dokumen 1
Komentar Evaluasi Dokumen 2
```

Kolom opsional untuk pengiriman email:

```text
Email
```

Catatan: file Excel berisi data kerja atau data pribadi tidak disimpan di Git. Semua file Excel sudah dimasukkan ke `.gitignore`.

## Alur Penggunaan

1. Buka tab `Upload Data`, lalu upload file Excel hasil review.
2. Periksa data pada tab `Preview Data`.
3. Buka tab `Generate PDF`, lalu buat PDF preview atau generate semua PDF.
4. Download PDF satuan atau ZIP semua PDF jika diperlukan.
5. Buka tab `Kirim Email`, isi konfigurasi SMTP, lakukan test kirim, lalu kirim semua email.

## Konfigurasi Email

Untuk Gmail, gunakan:

```text
SMTP Host: smtp.gmail.com
SMTP Port: 587
STARTTLS: aktif
AUTH: aktif
```

Gunakan App Password, bukan password utama akun Google.

Jika memakai server email kampus yang tidak mendukung login SMTP, matikan opsi `Gunakan login SMTP (AUTH)` di aplikasi.

## Catatan Keamanan

- Jangan commit file Excel, PDF hasil generate, ZIP, password SMTP, atau file `.env`.
- Simpan kredensial email hanya di environment lokal atau input langsung melalui UI aplikasi.
- Pastikan alamat email penerima sudah benar sebelum menjalankan kirim massal.
