#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAFA NEWS 브리핑 이메일 발송 모듈

환경변수 (우선순위 순):
  GMAIL_APP_PASSWORD  Gmail 앱 비밀번호 (가장 안정적 — Gmail→Gmail 직발송)
  RESEND_API_KEY      Resend API 키
  SENDGRID_API_KEY    SendGrid API 키
  EMAIL_TO            수신자 (기본: fafaai3377@gmail.com)
  GMAIL_USER          발신 Gmail 주소 (GMAIL_APP_PASSWORD 사용 시, 기본: fafaai3377@gmail.com)

사용:  python3 send_email.py "output/260529_FAFA NEWS.pdf" "2026년 5월 29일 (금)"
"""
import os, sys, base64, json, datetime, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import requests

DEFAULT_TO   = "fafaai3377@gmail.com"
DEFAULT_FROM = "FAFA NEWS <onboarding@resend.dev>"

def _subject(date_label):
    return f"[FAFA NEWS] {date_label} AI·디자인·마케팅 모닝 브리핑"

def _html(date_label):
    window = "72시간" if datetime.date.today().weekday() == 0 else "24시간"
    return (
        f"<div style='font-family:Apple SD Gothic Neo,Pretendard,sans-serif;color:#3a3a3a'>"
        f"<h2 style='margin:0 0 8px'>오늘의 AI·디자인·마케팅 브리핑</h2>"
        f"<p style='margin:0;color:#8a8a82'>{date_label}</p>"
        f"<p>지난 {window} 주요 소식을 카드 28장 PDF로 정리했습니다. 첨부파일을 확인하세요.</p>"
        f"<p style='color:#8a8a82;font-size:13px'>— FAFA NEWS 자동 브리핑</p></div>"
    )

def send_via_gmail(app_password, gmail_user, to, subject, html, pdf_path):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"FAFA NEWS <{gmail_user}>"
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    with open(pdf_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=os.path.basename(pdf_path))
    part["Content-Disposition"] = f'attachment; filename="{os.path.basename(pdf_path)}"'
    msg.attach(part)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, app_password)
        smtp.sendmail(gmail_user, to, msg.as_bytes())
    return 200, "OK"

def send_via_resend(api_key, sender, to, subject, html, pdf_path):
    with open(pdf_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps({
            "from": sender, "to": [to], "subject": subject, "html": html,
            "attachments": [{"filename": os.path.basename(pdf_path), "content": content}],
        }), timeout=30)
    return r.status_code, r.text

def send_via_sendgrid(api_key, sender, to, subject, html, pdf_path):
    with open(pdf_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    addr = sender.split("<")[-1].rstrip(">").strip() if "<" in sender else sender
    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps({
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": addr, "name": "FAFA NEWS"},
            "subject": subject,
            "content": [{"type": "text/html", "value": html}],
            "attachments": [{"filename": os.path.basename(pdf_path),
                             "type": "application/pdf", "content": content}],
        }), timeout=30)
    return r.status_code, r.text

def send(pdf_path, date_label, to=None, sender=None):
    to     = to     or os.environ.get("EMAIL_TO", DEFAULT_TO)
    sender = sender or os.environ.get("EMAIL_FROM", DEFAULT_FROM)
    subject, html = _subject(date_label), _html(date_label)

    gmail_pw   = os.environ.get("GMAIL_APP_PASSWORD")
    gmail_user = os.environ.get("GMAIL_USER", DEFAULT_TO)
    resend     = os.environ.get("RESEND_API_KEY")
    sg         = os.environ.get("SENDGRID_API_KEY")

    if gmail_pw:
        try:
            code, body = send_via_gmail(gmail_pw, gmail_user, to, subject, html, pdf_path)
            print(f"[Gmail SMTP] 200 OK -> {to}")
            return True
        except Exception as e:
            print(f"[Gmail SMTP] 실패: {e} — Resend로 재시도")

    if resend:
        code, body = send_via_resend(resend, sender, to, subject, html, pdf_path)
        ok = code in (200, 201)
        print(f"[Resend] {code} {'OK -> ' + to if ok else body[:300]}")
        return ok

    if sg:
        code, body = send_via_sendgrid(sg, sender, to, subject, html, pdf_path)
        ok = code in (200, 202)
        print(f"[SendGrid] {code} {'OK -> ' + to if ok else body[:300]}")
        return ok

    print("이메일 API 키 없음 (GMAIL_APP_PASSWORD / RESEND_API_KEY / SENDGRID_API_KEY 미설정) — 발송 건너뜀")
    return False

if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else None
    label = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().strftime("%Y년 %m월 %d일")
    if not pdf or not os.path.exists(pdf):
        print("PDF 경로를 인자로 주세요"); sys.exit(1)
    sys.exit(0 if send(pdf, label) else 2)
