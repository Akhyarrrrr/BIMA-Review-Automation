import smtplib

from pdf_generator import load_pdf_entry_bytes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import re


def is_valid_email(email):
    if not email or email == "-":
        return False
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email.strip()))


def send_email(
    smtp_host,
    smtp_port,
    smtp_user,
    smtp_password,
    use_tls,
    recipient_email,
    recipient_name,
    pt_name,
    pdf_bytes,
    attachment_name,
    sender_name="Tim Reviewer BIMA",
    body_extra="",
    require_auth=True,
):
    msg = MIMEMultipart()
    from_addr = (smtp_user or "").strip()
    if from_addr:
        msg["From"] = f"{sender_name} <{from_addr}>"
    else:
        msg["From"] = sender_name
    msg["To"] = recipient_email
    msg["Subject"] = f"Komentar Reviewer Usulan BIMA - {recipient_name}"

    extra = (body_extra or "").strip()
    extra_block = f"\n{extra}\n\n" if extra else "\n"

    body = f"""Yth. {recipient_name}
{pt_name}
{extra_block}Bersama ini kami sampaikan komentar reviewer terhadap protokol penelitian & pengabdian yang diusulkan ke Bima.

Silakan buka lampiran PDF untuk melihat detail komentar reviewer.

Hormat kami,
{sender_name}
"""
    msg.attach(MIMEText(body, "plain", "utf-8"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
    msg.attach(part)

    server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    try:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        auth_supported = bool(server.has_extn("auth"))
        if require_auth:
            if not auth_supported:
                return False, "SMTP AUTH extension not supported by server"
            server.login(smtp_user, smtp_password)
        elif auth_supported and smtp_user and smtp_password:
            # Jika server mendukung AUTH, boleh login opsional.
            server.login(smtp_user, smtp_password)

        sender_addr = from_addr or "no-reply@localhost"
        server.sendmail(sender_addr, recipient_email, msg.as_string())
        return True, "Berhasil dikirim"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_all_emails(
    smtp_config, pdf_data_dict, progress_callback=None, body_extra=""
):
    """
    pdf_data_dict: {label: {pdf_path, nidn, nama_ketua, pt, email, ...}}
    smtp_config: {host, port, user, password, use_tls, sender_name}
    progress_callback: callable(current, total, nama, status)
    body_extra: teks tambahan setelah salam (opsional)
    """
    results = []
    total = len(pdf_data_dict)

    for i, (_label, data) in enumerate(pdf_data_dict.items()):
        nama_ketua = data.get("nama_ketua", "")
        email = data.get("email", "-")

        if not is_valid_email(email):
            status = "skip"
            msg = "Email tidak tersedia"
        else:
            attachment_name = data.get("attachment_name") or (
                f"Komentar_Reviewer_{str(nama_ketua).replace(' ', '_')}.pdf"
            )
            pdf_bytes = load_pdf_entry_bytes(data)
            if not pdf_bytes:
                status = "error"
                msg = "File PDF tidak ditemukan di disk"
                results.append(
                    {
                        "nama_ketua": nama_ketua,
                        "nidn": data.get("nidn", "-"),
                        "email": email,
                        "status": status,
                        "pesan": msg,
                    }
                )
                if progress_callback:
                    progress_callback(i + 1, total, nama_ketua, status)
                continue

            ok, msg = send_email(
                smtp_host=smtp_config["host"],
                smtp_port=smtp_config["port"],
                smtp_user=smtp_config["user"],
                smtp_password=smtp_config["password"],
                use_tls=smtp_config.get("use_tls", True),
                recipient_email=email,
                recipient_name=nama_ketua,
                pt_name=data.get("pt", ""),
                pdf_bytes=pdf_bytes,
                attachment_name=attachment_name,
                sender_name=smtp_config.get("sender_name", "Tim Reviewer BIMA"),
                body_extra=body_extra,
                require_auth=smtp_config.get("require_auth", True),
            )
            status = "ok" if ok else "error"

        results.append(
            {
                "nama_ketua": nama_ketua,
                "nidn": data.get("nidn", "-"),
                "email": email,
                "status": status,
                "pesan": msg,
            }
        )

        if progress_callback:
            progress_callback(i + 1, total, nama_ketua, status)

    return results
