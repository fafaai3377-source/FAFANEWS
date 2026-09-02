#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAFA NEWS — 디자인·마케팅 모닝 브리핑 캐러셀 렌더러
- 표지 1 + 디자인 10 + 마케팅 10 = 21장 (1080x1350 PNG)
- 카드당 실제 기사 이미지(og:image) 1개 자동 삽입
- 클릭 가능한 출처 링크 포함 PDF 출력 (파일명: YYMMDD_FAFA NEWS.pdf)
- 디자인 시스템: 파스텔 옐로우 표지 + 차콜 + Pretendard (참고 표지 기준)

매 실행 시 DATE / DESIGN / MARKETING 리스트만 교체하면 동일 포맷으로 재생성된다.
"""
import os, re, io, html, datetime, hashlib
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------- 설정
FONT_DIR = "/tmp/fonts"
OUT      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
IMG_CACHE= os.path.join(OUT, "_img")
os.makedirs(OUT, exist_ok=True)
os.makedirs(IMG_CACHE, exist_ok=True)

W, H, M = 1080, 1350, 80

# 디자인 시스템 (참고 표지에서 추출)
YELLOW = (247, 237, 161)   # 표지 배경 파스텔 옐로우
CREAM  = (252, 250, 240)   # 내지 배경
INK    = (58, 58, 58)      # 차콜 (메인 타이틀)
INK2   = (90, 90, 86)      # 본문
GRAY   = (146, 146, 136)   # 보조 텍스트
VIOLET = (107, 78, 230)    # AI 액센트
BLUE   = (58, 96, 232)     # 디자인 액센트
CORAL  = (240, 96, 64)     # 마케팅 액센트
LINE   = (230, 228, 216)
WHITE  = (255, 255, 255)

F = {"black":"Pretendard-Black.otf","extrabold":"Pretendard-ExtraBold.otf",
     "bold":"Pretendard-Bold.otf","semibold":"Pretendard-SemiBold.otf",
     "medium":"Pretendard-Medium.otf","regular":"Pretendard-Regular.otf"}

def ensure_fonts():
    """Pretendard(한글) 폰트가 없으면 npm으로 자동 설치. 그래도 없으면 에러로 중단
    — 한글이 □□□(두부)로 깨진 PDF가 만들어지는 사고를 원천 차단한다."""
    import subprocess, glob
    need = os.path.join(FONT_DIR, F["black"])
    if os.path.exists(need):
        return
    os.makedirs(FONT_DIR, exist_ok=True)
    try:
        subprocess.run("npm install pretendard@1.3.9", cwd=FONT_DIR, shell=True,
                       check=False, capture_output=True, timeout=180)
        for f in glob.glob(os.path.join(FONT_DIR, "node_modules/pretendard/dist/public/static/*.otf")):
            dst = os.path.join(FONT_DIR, os.path.basename(f))
            if not os.path.exists(dst):
                import shutil; shutil.copy(f, dst)
    except Exception as e:
        print("폰트 설치 시도 실패:", e)
    if not os.path.exists(need):
        raise RuntimeError(
            f"Pretendard 폰트를 찾을 수 없습니다 ({need}). 한글이 깨지므로 렌더링을 중단합니다. "
            "`cd /tmp/fonts && npm install pretendard@1.3.9 && "
            "cp node_modules/pretendard/dist/public/static/*.otf /tmp/fonts/` 실행 후 재시도하세요.")

ensure_fonts()
def font(w, s): return ImageFont.truetype(os.path.join(FONT_DIR, F[w]), s)

UA = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# ---------------------------------------------------------------- 텍스트 헬퍼
def _break_word(d, word, f, mw):
    out, ln = [], ""
    for ch in word:
        if d.textlength(ln + ch, font=f) <= mw: ln += ch
        else:
            if ln: out.append(ln)
            ln = ch
    if ln: out.append(ln)
    return out

def wrap(d, t, f, mw):
    """띄어쓰기 단위 줄바꿈 — 단어가 폭을 넘으면 그 단어만 글자 단위로 분할."""
    out = []
    for para in t.split("\n"):
        ln = ""
        for word in para.split(" "):
            cand = word if not ln else ln + " " + word
            if d.textlength(cand, font=f) <= mw:
                ln = cand
            else:
                if ln: out.append(ln); ln = ""
                if d.textlength(word, font=f) <= mw:
                    ln = word
                else:
                    parts = _break_word(d, word, f, mw)
                    out.extend(parts[:-1]); ln = parts[-1] if parts else ""
        out.append(ln)
    return out

def dl(d, x, y, t, f, fill, mw, gap, maxlines=None):
    a, de = f.getmetrics(); lh = a + de + gap
    lines = wrap(d, t, f, mw)
    if maxlines and len(lines) > maxlines:
        lines = lines[:maxlines]
        while lines and d.textlength(lines[-1] + "…", font=f) > mw:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    for ln in lines:
        d.text((x, y), ln, font=f, fill=fill); y += lh
    return y

def truncate(d, t, f, mw):
    if d.textlength(t, font=f) <= mw: return t
    while t and d.textlength(t + "…", font=f) > mw: t = t[:-1]
    return t + "…"

def pill(d, x, y, t, f, bg, fg, px=24, py=12):
    tw = d.textlength(t, font=f); a, de = f.getmetrics(); th = a + de
    d.rounded_rectangle([x, y, x+tw+px*2, y+th+py*2], radius=(th+py*2)//2, fill=bg)
    d.text((x+px, y+py-2), t, font=f, fill=fg)
    return x+tw+px*2

# ---------------------------------------------------------------- 이미지 페치
def _abs_url(src, base_url):
    src = html.unescape(src).strip()
    if src.startswith("//"): return "https:" + src
    if src.startswith("/"):
        from urllib.parse import urlparse
        p = urlparse(base_url); return f"{p.scheme}://{p.netloc}" + src
    return src

def _load_image(src):
    try:
        ir = requests.get(src, headers=UA, timeout=12)
        ct = ir.headers.get("Content-Type", "")
        if ir.status_code != 200 or len(ir.content) < 3000: return None
        if "svg" in ct or src.lower().endswith(".svg"): return None
        img = Image.open(io.BytesIO(ir.content)).convert("RGB")
        if img.width < 200 or img.height < 150: return None  # 로고·아이콘 배제
        return img
    except Exception:
        return None

def fetch_og_image(url):
    """기사 페이지에서 대표 이미지 추출 — 여러 메타/JSON-LD/본문 이미지를 순차 시도."""
    try:
        r = requests.get(url, headers=UA, timeout=12)
        if r.status_code != 200: return None
        h = r.text
    except Exception:
        return None
    cands = []
    for pat in (
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
        r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)',
    ):
        cands += re.findall(pat, h, re.I)
    # JSON-LD "image": "..." 또는 "image": ["..."]
    for m in re.findall(r'"image"\s*:\s*(\[[^\]]+\]|"[^"]+")', h, re.I):
        cands += re.findall(r'https?:[^"\']+', m)
    # 본문 내 큰 이미지(article 우선)
    body = re.search(r'<article[\s\S]{0,40000}?</article>', h, re.I)
    scope = body.group(0) if body else h
    cands += re.findall(r'<img[^>]+(?:data-src|src)=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)', scope, re.I)
    seen = set()
    for c in cands:
        src = _abs_url(c, url)
        if src in seen: continue
        seen.add(src)
        img = _load_image(src)
        if img: return img
    return None

# 한국어 브랜드·인물명 → 영어 변환 (Openverse 검색 품질 향상)
KO_TO_EN = {
    "안드레이 카파시": "Andrej Karpathy",
    "카파시": "Karpathy",
    "구글": "Google", "알파벳": "Alphabet Google",
    "엔비디아": "NVIDIA", "엔비디아의": "NVIDIA",
    "마이크로소프트": "Microsoft", "MS": "Microsoft",
    "깃허브": "GitHub", "코파일럿": "GitHub Copilot",
    "오픈AI": "OpenAI", "ChatGPT": "ChatGPT",
    "앤트로픽": "Anthropic", "Anthropic": "Anthropic",
    "피그마": "Figma", "Figma": "Figma",
    "애플": "Apple", "메타": "Meta",
    "아마존": "Amazon", "AWS": "AWS",
    "스냅": "Snapchat Snap",
    "타입폼": "Typeform",
    "펍매틱": "PubMatic programmatic",
    "세일즈포스": "Salesforce",
    "틱톡": "TikTok",
    "유튜브": "YouTube",
    "링크드인": "LinkedIn",
    "트위터": "Twitter X",
    "펩시코": "PepsiCo",
    "루프트한자": "Lufthansa",
    "BMW": "BMW",
    "BBH": "BBH advertising agency",
    "리브랜드": "rebrand identity",
    "브랜딩": "branding",
    "광고": "advertising",
    "캠페인": "marketing campaign",
    "에이전트": "AI agent",
    "펀딩": "startup funding",
    "밸류에이션": "startup valuation",
    "거버넌스": "AI governance policy",
    "크리에이터": "creator content",
}

def _img_queries(title, cat_en):
    """제목에서 구체적 검색어 추출 → 카테고리 폴백 순서로 반환.
    한국어 브랜드·인물명을 영어로 변환해 Openverse 검색 정밀도를 높인다."""
    # 한국어 → 영어 치환
    t = title
    for ko, en in KO_TO_EN.items():
        t = t.replace(ko, en)
    # 영어 단어 추출 (3글자 이상, 숫자·기호 제외)
    en_words = re.findall(r"[A-Z][A-Za-z]{2,}|[A-Za-z]{4,}", t)
    # 브랜드명·고유명사(대문자 시작) 우선
    proper = [w for w in en_words if w[0].isupper()]
    common = [w for w in en_words if not w[0].isupper()]
    fallback = {"AI":         ["artificial intelligence", "AI technology", "machine learning"],
                "DESIGN":     ["graphic design", "brand identity", "design studio"],
                "MARKETING":  ["marketing advertising", "brand campaign", "digital marketing"],
                }.get(cat_en, ["technology"])
    qs = []
    # 가장 구체적: 고유명사 2개 조합
    if len(proper) >= 2: qs.append(f"{proper[0]} {proper[1]}")
    if proper:           qs.append(proper[0])
    # 고유명사 + 카테고리 힌트
    if proper:           qs.append(f"{proper[0]} {fallback[0]}")
    # 일반 단어 + 힌트
    if common:           qs.append(f"{common[0]} {fallback[0]}")
    qs += fallback
    seen = set()
    return [q for q in qs if not (q in seen or seen.add(q))]

def fetch_related_image(queries, salt=0):
    """og:image 실패 시 Openverse(무료 이미지 검색)로 주제 관련 실사진을 가져온다.
    salt로 결과 순서를 회전시켜 카드마다 다른 이미지를 고른다."""
    if isinstance(queries, str): queries = [queries]
    for q in queries:
        try:
            r = requests.get("https://api.openverse.org/v1/images/",
                             params={"q": q, "page_size": 12, "mature": "false"},
                             headers=UA, timeout=12)
            if r.status_code != 200: continue
            results = r.json().get("results", [])
        except Exception:
            continue
        if not results: continue
        n = len(results); off = salt % n
        for i in [(off + j) % n for j in range(n)]:
            res = results[i]
            for u in (res.get("url"), res.get("thumbnail")):
                if not u: continue
                img = _load_image(u)
                if img: return img
    return None

def cover_crop(img, bw, bh):
    iw, ih = img.size
    scale = max(bw/iw, bh/ih)
    nw, nh = int(iw*scale)+1, int(ih*scale)+1
    img = img.resize((nw, nh), Image.LANCZOS)
    x = (nw-bw)//2; y = (nh-bh)//2
    return img.crop((x, y, x+bw, y+bh))

def placeholder(accent, label, bw, bh, seed=""):
    # seed로 그라데이션 방향·색을 다르게 해 카드마다 고유한 플레이스홀더 생성
    s = int(hashlib.md5((seed or label).encode()).hexdigest(), 16)
    shift = 28 + (s % 46)
    c2 = tuple(min(255, c + shift) for c in accent)
    base = Image.new("RGB", (bw, bh), accent)
    top  = Image.new("RGB", (bw, bh), c2)
    mask = Image.linear_gradient("L").resize((bw, bh))
    if s & 1:   mask = mask.transpose(Image.FLIP_TOP_BOTTOM)
    if s & 2:   mask = mask.rotate(90, expand=True).resize((bw, bh))
    img = Image.composite(base, top, mask)
    d = ImageDraw.Draw(img)
    f = font("extrabold", 56)
    tw = d.textlength(label, font=f)
    d.text(((bw-tw)//2, bh//2-40), label, font=f, fill=(255,255,255))
    f2 = font("medium", 30)
    sub = "FAFA NEWS"
    d.text(((bw-d.textlength(sub, font=f2))//2, bh//2+40), sub, font=f2, fill=(255,255,255))
    return img

# 같은 사진이 두 번 들어가지 않도록 사용한 이미지 해시를 추적
_SEEN_HASHES = set()

def _ihash(img):
    return hashlib.md5(img.resize((64, 64)).tobytes()).hexdigest()

def get_card_image(url, accent, source, bw, bh, key, title="", cat_en="", force_search=False):
    """force_search=True: og:image를 건너뛰고 바로 제목 기반 이미지 검색(동일 URL 중복 카드용)."""
    cache = os.path.join(IMG_CACHE, key + ".png")
    salt  = int(hashlib.md5(key.encode()).hexdigest(), 16)
    qs    = _img_queries(title, cat_en)
    if os.path.exists(cache):
        img = Image.open(cache).convert("RGB")
    else:
        if force_search:
            fetched = fetch_related_image(qs, salt)
        else:
            fetched = fetch_og_image(url) or fetch_related_image(qs, salt)
        img = cover_crop(fetched, bw, bh) if fetched else placeholder(accent, source, bw, bh, key)
        img.save(cache)
    # 중복(동일 사진) 감지 → 다른 salt로 재검색, 그래도 겹치면 플레이스홀더
    if _ihash(img) in _SEEN_HASHES:
        salt2 = int(hashlib.md5((key + "alt").encode()).hexdigest(), 16)
        alt   = fetch_related_image(qs, salt2)
        img   = cover_crop(alt, bw, bh) if (alt and _ihash(cover_crop(alt, bw, bh)) not in _SEEN_HASHES) \
                else placeholder(accent, source, bw, bh, key)
        img.save(cache)
    _SEEN_HASHES.add(_ihash(img))
    return img

# ---------------------------------------------------------------- 카드
def base(bg=CREAM):
    im = Image.new("RGB", (W, H), bg); return im, ImageDraw.Draw(im)

def footer(im, d, idx, total, ac):
    dot, gap = 12, 10; tw = total*dot + (total-1)*gap
    x = (W-tw)//2; y = H-58
    for i in range(total):
        d.ellipse([x, y, x+dot, y+dot], fill=(ac if i == idx-1 else LINE)); x += dot+gap
    f = font("bold", 24)
    d.text((W-M-d.textlength("FAFA NEWS", font=f), y-4), "FAFA NEWS", font=f, fill=GRAY)
    d.text((M, y-4), f"{idx:02d} / {total:02d}", font=f, fill=GRAY)

def cover(date_str, count):
    im, d = base(YELLOW)
    d.text((M, 96), "Morning Brief", font=font("bold", 42), fill=INK)
    d.text((M, 156), date_str, font=font("bold", 42), fill=INK)
    fn = font("bold", 42); d.text((W-M-d.textlength("FAFA NEWS", font=fn), 96), "FAFA NEWS", font=fn, fill=GRAY)
    big = font("black", 150); y = 380
    for ln in ["오늘의", "디자인", "마케팅", "브리핑"]:
        d.text((M, y), ln, font=big, fill=INK); y += 168
    d.text((M, H-118), f"지난 24시간 AI·디자인·마케팅 소식 {count}건", font=font("medium", 32), fill=(120,118,96))
    nav = "넘겨서 보기 →"; fn2 = font("bold", 38)
    d.text((W-M-d.textlength(nav, font=fn2), H-122), nav, font=fn2, fill=INK)
    p = os.path.join(OUT, "01_cover.png"); im.save(p)
    return p, None

def closing(fn):
    im, d = base(YELLOW)
    d.text((M, 96), "Outro", font=font("bold", 42), fill=INK)
    fn0 = font("bold", 42); d.text((W-M-d.textlength("FAFA NEWS", font=fn0), 96), "FAFA NEWS", font=fn0, fill=GRAY)
    big = font("black", 150); y = 430
    for ln in ["오늘", "하루도", "파이팅!"]:
        d.text((M, y), ln, font=big, fill=INK); y += 168
    d.text((M, y+24), "오늘의 브리핑은 여기까지 — 좋은 하루 보내세요.", font=font("medium", 38), fill=(120,118,96))
    d.text((M, H-118), "내일 아침 10시, 다시 만나요", font=font("medium", 32), fill=(120,118,96))
    nav = "FAFA NEWS"; fn2 = font("bold", 38)
    d.text((W-M-d.textlength(nav, font=fn2), H-122), nav, font=fn2, fill=INK)
    p = os.path.join(OUT, fn); im.save(p)
    return p, None

def card(idx, total, cat_en, cat_ko, ac, title, body, source, url, fn, force_search=False):
    im, d = base(CREAM)
    BH = 620
    img = get_card_image(url, ac, source, W, BH, fn.split(".")[0], title, cat_en, force_search)
    im.paste(img, (0, 0))
    # 이미지 위 그라데이션(가독성) — 상단 살짝 어둡게
    shade = Image.new("RGBA", (W, 180), (0,0,0,0))
    sd = ImageDraw.Draw(shade)
    for i in range(180):
        sd.line([(0,i),(W,i)], fill=(0,0,0,int(120*(1-i/180))))
    im.paste(Image.alpha_composite(im.crop((0,0,W,180)).convert("RGBA"), shade).convert("RGB"), (0,0))
    d = ImageDraw.Draw(im)
    # 카테고리 pill (이미지 위 좌상단)
    label = cat_en if cat_en == cat_ko else f"{cat_en} · {cat_ko}"
    pill(d, M, 40, label, font("bold", 28), ac, WHITE)
    # 본문 영역
    y = BH + 44
    y = dl(d, M, y, title, font("extrabold", 56), INK, W-2*M, 10, maxlines=3); y += 18
    d.rectangle([M, y, M+72, y+7], fill=ac); y += 40
    dl(d, M, y, body, font("regular", 36), INK2, W-2*M, 14, maxlines=4)
    # 출처 + 클릭 가능 링크
    sy = H-176
    d.text((M, sy), "출처", font=font("bold", 26), fill=ac)
    d.text((M+72, sy), source, font=font("semibold", 30), fill=INK)
    ly = sy + 46
    disp = truncate(d, url.replace("https://","").replace("http://",""), font("medium", 26), W-2*M-40)
    d.text((M, ly), "↗ " + disp, font=font("medium", 26), fill=BLUE)
    lw = d.textlength("↗ " + disp, font=font("medium", 26))
    link_rect = (M, ly-4, M+lw, ly+38)   # 픽셀 좌표 (PDF 링크용)
    footer(im, d, idx, total, ac)
    p = os.path.join(OUT, fn); im.save(p)
    return p, (link_rect, url)

# ---------------------------------------------------------------- PDF (클릭 가능 링크)
def build_pdf(pages, pdf_path):
    import fitz
    doc = fitz.open()
    for png, link in pages:
        pg = doc.new_page(width=W, height=H)
        buf = io.BytesIO()
        Image.open(png).convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
        pg.insert_image(fitz.Rect(0, 0, W, H), stream=buf.getvalue())
        if link:
            rect, url = link
            pg.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(*rect), "uri": url})
    doc.save(pdf_path); doc.close()

# ================================================================ 데이터 (매 실행 교체)
# 분야별 7건씩. (제목, 한국어요약, 출처명, 원문 URL)
# 날짜는 한국 시간(KST) 기준 '오늘'로 자동 설정 — 스케줄 실행 시 날짜가 어긋나지 않게 한다.
# (특정 날짜로 고정하려면 아래 두 줄을 직접 값으로 바꾼다.)
_KST    = datetime.timezone(datetime.timedelta(hours=9))
_TODAY  = datetime.datetime.now(_KST).date()
_WD     = ["월", "화", "수", "목", "금", "토", "일"]
DATE_ISO = _TODAY
DATE = f"{_TODAY.year}년 {_TODAY.month}월 {_TODAY.day}일 ({_WD[_TODAY.weekday()]})"

AI = [
 ("앤트로픽, 클로드 무단 행동 사고 후 AI 훈련·보안 평가 일시 중단",
  "Anthropic이 7월 공개한 3건의 사고 이후 사전 출시 모델의 외부 사이버 평가와 일부 강화학습 훈련을 일시 중단했다고 9월 1일 밝혔다. 안전장치를 추가해 외부 평가는 재개했지만 고위험 환경 일부는 여전히 보류 중이다.",
  "IBTimes", "https://www.ibtimes.com/anthropic-spotted-unauthorized-actions-agents-it-pausing-some-training-evaluations-3807021"),
 ("美 국방부, GenAI.mil에 ChatGPT Mil·Grok 추가",
  "펜타곤이 8월 31일 챗GPT와 그록의 정부용 버전을 GenAI.mil 포털에 새로 추가했다. 국방부 인력 300만 명이 제미나이와 함께 세 가지 AI를 선택해 쓸 수 있게 됐다.",
  "TechCrunch", "https://techcrunch.com/2026/08/31/the-pentagon-now-has-its-own-version-of-chatgpt-and-grok/"),
 ("챗GPT 헬스, 에픽 전자의무기록 연동 시작",
  "오픈AI가 9월 1일 챗GPT 포 헬스케어에 에픽 EHR 연동을 추가했다고 발표했다. 승인된 임상의가 진료기록·검사결과·투약정보를 챗GPT 안에서 조회할 수 있으며, UCSF 헬스가 파일럿 파트너로 참여한다.",
  "TechCrunch", "https://techcrunch.com/2026/09/01/chatgpt-health-adds-epic-integration-for-clinicians-to-import-patient-data/"),
 ("세일즈포스·앤트로픽, '클로드포스' 파트너십 발표",
  "두 회사가 클로드가 세일즈포스 데이터와 워크플로를 직접 읽고 실행하는 '클로드포스'를 공개했다. 37개 사전 구축 영업 스킬을 담은 플러그인이 포함되며, 발표 당일 세일즈포스 주가가 시간외 약 12% 뛰었다.",
  "Reworked", "https://www.reworked.co/digital-workplace/salesforce-anthropic-launch-claudeforce-partnership/"),
 ("문샷AI, Kimi K3를 유일한 플래그십으로 — 구버전 모델 퇴역",
  "8월 31일 문샷AI의 2.8조 파라미터 모델 Kimi K3가 유일한 현역 플래그십이 됐다. 기존 Kimi K2.5와 moonshot-v1 시리즈는 서비스가 종료돼 호출 시 404 오류를 반환한다.",
  "BigGo Finance", "https://finance.biggo.com/news/87dc9670-9bba-43d7-83ef-6b3aff4af97c"),
 ("알리바바 Qwen, 차세대 Qwen4 아키텍처 조기 공개",
  "알리바바가 Qwen3.8-Flash-Next를 오픈웨이트로 공개하며 차세대 Qwen4 아키텍처를 미리 선보였다. 1250억 파라미터 중 60억만 활성화하는 구조로 코딩·업무 태스크에서 이전 모델을 크게 앞섰다.",
  "TechNode", "https://technode.com/2026/08/26/alibabas-qwen-to-open-source-qwen3-8-flash-next-previewing-qwen4-architecture/"),
 ("중국 3차원 생성 AI 스타트업 VAST, 시리즈B·B+로 약 30억 위안 조달",
  "생성형 3D 플랫폼 Tripo AI를 운영하는 중국 스타트업 VAST가 9월 1일 시리즈B·B+ 라운드로 약 30억 위안(약 4억 달러)을 조달했다고 발표했다. 매트릭스 파트너스 차이나 등이 참여했으며, 최근 6개월간 누적 조달액은 약 50억 위안에 달한다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/tripo-ai-raises-3-billion-yuan-in-series-b-and-series-b-funding-302866057.html"),
]

DESIGN = [
 ("AJ 벨, 종 그래픽 담은 새 브랜드 아이덴티티 공개",
  "영국 투자 플랫폼 AJ 벨이 벨(bell) 그래픽과 새 서체·컬러 팔레트를 적용한 브랜드 아이덴티티를 공개했다. 9월 5일부터 자사 D2C 플랫폼에 우선 적용되며 'Feel Good Investing' 캠페인과 함께 확장된다.",
  "International Adviser", "https://www.international-adviser.com/aj-bell-unveils-new-brand-identity/"),
 ("보다폰 아이디어, 샤룩 칸과 함께 새 로고 'Vi' 공개",
  "인도 통신사 보다폰 아이디어가 9월 1일 입자 형태였던 기존 로고를 둥글고 입체적인 3D 구체로 바꿨다. 배우 샤룩 칸을 브랜드 앰버서더로 발탁해 새 캠페인도 함께 시작한다.",
  "Business Standard", "https://www.business-standard.com/companies/news/vodafone-idea-onboards-shah-rukh-khan-as-brand-ambassador-unveils-new-logo-126090101320_1.html"),
 ("베이퍼레소 DOJO, 손글씨 로고 담은 새 글로벌 아이덴티티 공개",
  "베이퍼레소의 DOJO 브랜드가 9월 1일 손글씨 스타일 로고와 새 패키지·디지털 자산을 담은 글로벌 브랜드 아이덴티티를 출시했다. 향후 수개월에 걸쳐 제품 패키징과 마케팅 자료 전반에 적용된다.",
  "iGeekPhone", "https://www.igeekphone.com/dojo-by-vaporesso-to-introduce-new-global-brand-identity-on-september-1/"),
 ("보안기업 매트릭스, 글로벌 성장 위해 진화된 브랜드 아이덴티티 공개",
  "영상보안·출입통제 기업 매트릭스가 사업 확장에 맞춰 한층 정제된 새 브랜드 아이덴티티를 선보였다. 제품·패키징·디지털 채널·매장 사이니지 전반에 순차 적용된다.",
  "SDM Magazine", "https://www.sdmmag.com/articles/105747-matrix-unveils-evolved-brand-identity-marks-next-chapter-of-growth"),
 ("피그마, 생성형 플러그인·셰이더에 애니메이션·퍼블리싱 기능 추가",
  "피그마가 프롬프트만으로 만든 생성형 플러그인과 셰이더에 모션 타임라인 기반 애니메이션과 마우스 반응형 상호작용을 추가했다. 완성한 도구는 피그마 커뮤니티에 퍼블리싱해 공유할 수 있다.",
  "Figma", "https://www.figma.com/release-notes/"),
 ("깃피그, 피그마-깃허브 양방향 동기화 플러그인 출시",
  "디자인 토큰과 변수를 깃허브 저장소와 실시간으로 동기화하는 피그마 플러그인 '깃피그'가 공개됐다. 브랜치·커밋·풀리퀘스트 등 깃 워크플로를 피그마 안에서 그대로 사용할 수 있다.",
  "GitFig", "https://gitfig.com/"),
 ("어도비, 70여 개 크리에이티브 툴 담은 통합 챗GPT 플러그인 출시",
  "어도비가 포토샵·프리미어·파이어플라이 등 70여 개 도구를 하나로 묶은 통합 플러그인을 챗GPT에 선보였다. 채팅만으로 이미지 편집부터 캠페인 영상 제작까지 가능해졌다.",
  "BigGo Finance", "https://finance.biggo.com/news/ed529f32-9740-41af-9895-bab9c780d9ae"),
]

MARKETING = [
 ("메타, 9월 광고 업데이트 — 어드밴티지+ 완전 자동화로 전환",
  "메타가 9월 들어 구형 자동화 캠페인 유형을 단계적으로 폐지하고 어드밴티지+ 중심의 완전 자동화 체제로 전환한다. 메타 슈퍼인텔리전스랩의 이미지 생성 모델 '뮤즈 이미지'도 광고 소재 제작에 확대 적용된다.",
  "SuccessKnocks", "https://successknocks.com/meta-ads-update-september-2026/"),
 ("X, 크리에이터 수익배분 종료하고 '오리지널 콘텐츠 리워드' 도입",
  "X가 9월 7일 기존 수익배분 프로그램을 종료하고 9월 8일부터 원본 콘텐츠에만 보상하는 새 프로그램을 시작한다. 재게시·복사 콘텐츠로 수익을 얻던 어뷰징을 막기 위한 조치다.",
  "TheNextWeb", "https://thenextweb.com/news/x-original-content-rewards-program-revenue-sharing-ends"),
 ("구글 어시스턴트, 9월 4일부터 단계적 종료 — 제미나이로 전환",
  "구글이 안드로이드·웨어OS 등에서 어시스턴트를 순차 제거하고 제미나이로 완전히 대체한다고 밝혔다. 음성 커머스·브랜드 보이스 광고를 운용해온 마케터들은 새로운 대응 전략이 필요해졌다.",
  "Android Authority", "https://www.androidauthority.com/google-assistant-shutdown-3694622/"),
 ("어센던트 네트워크, 뉴욕서 리테일 미디어 업프론트 'SHOWCASE' 개최",
  "어센던트 네트워크가 9월 2일 뉴욕 타임스센터에서 세계 최대 규모의 리테일·커머스 미디어 업프론트 'SHOWCASE'를 처음 연다. 퍼블리시스·덴쓰·WPP 등 주요 에이전시 의사결정권자와 리테일 미디어 네트워크가 한자리에 모인다.",
  "Yahoo Finance", "https://finance.yahoo.com/news/ascendant-network-host-inaugural-showcase-133800642.html"),
 ("6센스, MCP 서버로 AI 에이전트에 구매 인텐트 데이터 직접 연동",
  "B2B 마케팅 플랫폼 6센스가 클로드·챗GPT·에이전트포스 등 MCP 호환 AI 에이전트에 계정 인텐트·구매 단계 데이터를 바로 제공하는 MCP 서버를 정식 출시했다. 별도 커스텀 연동 없이 세일즈·마케팅 팀이 실시간 구매 신호를 활용할 수 있다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/6sense-brings-intelligence-directly-ai-163000696.html"),
 ("애드위크, 2026 '브랜드 지니어스' 수상 브랜드 발표",
  "애드위크가 9월 1일 자 매거진에서 창의성과 회복력으로 업계를 바꾼 브랜드와 리더들을 '2026 브랜드 지니어스'로 선정해 소개했다.",
  "Adweek", "https://www.adweek.com/creativity/brand-genius-2026-winners-changing-industries-creativity-resilience/"),
 ("인플루언서 광고, 2026년 마케터 최우선 순위로 부상",
  "이마케터 조사에서 응답자의 57%가 인플루언서 광고·파트너십을 올해 최우선 과제로 꼽아 전년 48%보다 크게 늘었다. AI 시대에 '사람다운 스토리텔링'에 대한 수요가 커진 결과로 풀이된다.",
  "eMarketer", "https://www.emarketer.com/content/influencer-ads-emerge-buyers--top-ad-priority-2026"),
]

# (영문 라벨, 한글 라벨, 액센트, 파일 접미사, 기사 리스트)
SECTIONS = [
 ("AI", "AI", VIOLET, "ai", AI),
 ("DESIGN", "디자인", BLUE, "design", DESIGN),
 ("MARKETING", "마케팅", CORAL, "marketing", MARKETING),
]

# ================================================================ 실행
def main():
    pages = []
    n_articles = sum(len(s[4]) for s in SECTIONS)
    total = 1 + n_articles + 1   # 표지 + 기사 + 엔딩
    # 동일 URL을 2번 이상 쓰는 카드는 og:image가 무관한 이미지일 가능성이 높음
    # → 첫 번째 카드만 og:image, 나머지는 제목 기반 검색 강제
    all_urls = [u for _, _, _, _, items in SECTIONS for _, _, _, u in items]
    _seen_urls: set = set()
    def _force(url):
        if url in _seen_urls: return True
        _seen_urls.add(url); return False
    pages.append(cover(DATE, n_articles))
    idx = 2
    for cat_en, cat_ko, ac, suffix, items in SECTIONS:
        for t, b, s, u in items:
            pages.append(card(idx, total, cat_en, cat_ko, ac, t, b, s, u,
                              f"{idx:02d}_{suffix}.png", force_search=_force(u)))
            idx += 1
    pages.append(closing(f"{idx:02d}_closing.png"))
    pdf_name = DATE_ISO.strftime("%y%m%d") + "_FAFA NEWS.pdf"
    pdf_path = os.path.join(OUT, pdf_name)
    build_pdf(pages, pdf_path)
    print("PNG:", total, "장")
    print("PDF:", pdf_path)
    # 이메일 자동 발송 (RESEND_API_KEY / SENDGRID_API_KEY 설정 시)
    try:
        import send_email
        send_email.send(pdf_path, DATE)
    except Exception as e:
        print("이메일 발송 생략:", e)

if __name__ == "__main__":
    main()
