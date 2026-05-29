#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAFA NEWS 브리핑 이메일 발송 모듈 (HTTPS API 사용 — 이 환경은 SMTP 차단됨)

환경변수:
  RESEND_API_KEY      Resend API 키 (권장)  또는
  SENDGRID_API_KEY    SendGrid API 키
  EMAIL_TO            수신자 (기본: taeo@foundfounded.com)
  EMAIL_FROM          발신자 (기본: FAFA NEWS <onboarding@resend.dev>)
                      ※ 커스텀 도메인 발신은 해당 서비스에서 도메인 인증 필요

사용:  python3 send_email.py "output/260529_FAFA NEWS.pdf" "2026년 5월 29 (금)"
또는   render_carousel.py 가 끝에서 send() 호출
"""
import os, sys, base64, json, datetime
import requests

DEFAULT_TO   = "taeo@foundfounded.com"
DEFAULT_FROM = "FAFA NEWS <onboarding@resend.dev>"

def _subject(date_label):
    return f"[FAFA NEWS] {date_label} AI·디자인·마케팅 모닝 브리핑"

def _html(date_label):
    return (
        f"<div style='font-family:Apple SD Gothic Neo,Pretendard,sans-serif;color:#3a3a3a'>"
        f"<h2 style='margin:0 0 8px'>오늘의 AI·디자인·마케팅 브리핑</h2>"
        f"<p style='margin:0;color:#8a8a82'>{date_label}</p>"
        f"<p>지난 24시간 주요 소식 21건을 카드 23장 PDF로 정리했습니다. 첨부파일을 확인하세요.</p>"
        f"<p style='color:#8a8a82;font-size:13px'>— FAFA NEWS 자동 브리핑</p></div>"
    )

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
    to = to or os.environ.get("EMAIL_TO", DEFAULT_TO)
    sender = sender or os.environ.get("EMAIL_FROM", DEFAULT_FROM)
    subject, html = _subject(date_label), _html(date_label)
    resend = os.environ.get("RESEND_API_KEY")
    sg = os.environ.get("SENDGRID_API_KEY")
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
    print("이메일 API 키 없음 (RESEND_API_KEY 또는 SENDGRID_API_KEY 미설정) — 발송 건너뜀")
    return False

if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else None
    label = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().strftime("%Y년 %m월 %d일")
    if not pdf or not os.path.exists(pdf):
        print("PDF 경로를 인자로 주세요"); sys.exit(1)
    sys.exit(0 if send(pdf, label) else 2)
