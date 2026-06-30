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
 ("Claude Fable 5 & Mythos 5 공개 — Mythos급 성능 일반 배포",
  "앤트로픽이 6월 9일 Claude Fable 5와 Mythos 5를 공개했다. Fable 5는 역대 일반 배포 모델 중 최고 성능으로, 소프트웨어 엔지니어링·시각·과학 연구 등 거의 모든 벤치마크에서 최고 수준을 기록했다. 미국 정부의 수출 통제 지침으로 일시 접근이 제한됐다.",
  "Anthropic", "https://www.anthropic.com/news/claude-fable-5-mythos-5"),
 ("Anthropic 650억 달러 조달, 밸류 9,650억 달러 — 10월 IPO 준비",
  "앤트로픽이 알티미터·시쿼이아 주도로 650억 달러 시리즈 H를 9,650억 달러 기업가치에 완료하며 OpenAI를 넘어 AI 스타트업 최고 밸류를 기록했다. 6월 1일 비공개 S-1을 제출하고 10월 나스닥 IPO를 목표로 하며, 연간 반복 매출(ARR)은 470억 달러를 돌파했다.",
  "TechCrunch", "https://techcrunch.com/2026/05/28/anthropic-raises-65-billion-nears-1t-valuation-ahead-of-ipo/"),
 ("OpenAI·NVIDIA 10GW AI 인프라 파트너십 — NVIDIA 100억 달러 투자",
  "OpenAI와 NVIDIA가 차세대 AI 모델 학습·운영을 위해 최소 10기가와트 규모 NVIDIA 시스템 구축 전략적 파트너십을 발표했다. NVIDIA는 구축 규모에 비례해 최대 1,000억 달러를 단계적으로 투자하며, 첫 1GW는 2026년 하반기 Vera Rubin 플랫폼으로 가동된다.",
  "NVIDIA Newsroom", "https://nvidianews.nvidia.com/news/openai-and-nvidia-announce-strategic-partnership-to-deploy-10gw-of-nvidia-systems"),
 ("GPT-5.5 정식 출시 — 에이전틱 코딩 Terminal-Bench 82.7%",
  "OpenAI가 4월 23일 GPT-5.5를 공개했다. Terminal-Bench 2.0 82.7%, 장문 컨텍스트 MRCR v2(512K~1M 토큰) 74.0% 등 에이전틱 코딩·컴퓨터 사용에서 최고 수준을 기록했다. 5월 5일부터 무료 사용자에게도 GPT-5.5 Instant가 기본 모델로 제공된다.",
  "OpenAI", "https://www.buildfastwithai.com/blogs/gpt-5-5-review-benchmarks-2026"),
 ("Gemini 3.5 Pro 7월 GA 확정 — 200만 토큰·Deep Think 탑재",
  "구글이 6월 29일 Gemini 3.5 Pro의 7월 정식 출시(GA)를 확정했다. 업계 최대인 200만 토큰 컨텍스트 윈도우(Flash 대비 2배)와 Deep Think 추론 모드를 탑재하며, 현재는 Vertex AI 기업 고객 한정 프리뷰로 제공 중이다.",
  "TechTimes", "https://www.techtimes.com/articles/319318/20260629/gemini-35-pro-cleared-july-launch-fable-5-nears-return-gpt-56-stays-locked.htm"),
 ("NVIDIA, 자율 진화형 기업 AI 에이전트 오픈 개발 플랫폼 공개",
  "NVIDIA가 지식 작업 자동화를 위한 오픈소스 에이전트 개발 플랫폼을 공개했다. NVIDIA OpenShell 런타임과 AI-Q Blueprint가 포함되며, 기업이 프런티어·오픈 모델 하이브리드로 전문 AI 에이전트를 직접 구축하고 쿼리 비용을 절반으로 줄일 수 있다.",
  "NVIDIA Newsroom", "https://nvidianews.nvidia.com/news/ai-agents"),
 ("Arcade, AI 에이전트 보안 플랫폼 6,000만 달러 시리즈 A 조달",
  "AI 에이전트 인가·거버넌스 인프라 스타트업 Arcade가 SYN Ventures 주도로 6,000만 달러 시리즈 A를 완료했다. 엔터프라이즈 환경에서 어떤 에이전트가 누구의 권한으로 어떤 리소스에 접근하는지 가시성과 제어를 제공하며, 최근 6개월간 플랫폼 툴 호출량이 25배 증가했다.",
  "PYMNTS", "https://www.pymnts.com/news/investment-tracker/2026/arcade-raises-60-million-to-control-ai-agents/"),
]

DESIGN = [
 ("Apple TV+ 리브랜드, '+' 삭제·유리 조각 로고 — 칸 라이언즈 디자인 그랑프리",
  "TBWA\\Media Arts Lab이 제작한 Apple TV+ 리브랜드가 칸 라이언즈 2026 디자인 그랑프리를 수상했다. '+' 기호를 삭제하고 손으로 제작한 유리 조각을 직접 촬영해 새 로고를 만들었으며, 구독자의 33%만 자사 콘텐츠를 식별하던 브랜드 인지 문제를 정면 돌파했다.",
  "LBBOnline", "https://lbbonline.com/news/Cannes-Lions-2026-Grand-Prix-Winners-in-Entertainment-Gaming-Music-Sport-Design-Digital-Craft-Film-Craft-and-Industry-Craft"),
 ("피그마 Make, 로컬 코드베이스 연결 — AI 코딩 에이전트로 코드 직접 수정",
  "5월 28일 피그마 Make가 로컬 코드베이스에 직접 연결되도록 확장됐다. 특정 요소를 지정해 프롬프트하거나 편집 패널·채팅으로 변경을 지시하면 AI 코딩 에이전트가 코드를 실시간으로 수정한다.",
  "Figma", "https://www.figma.com/release-notes/"),
 ("펩시코, 25년 만의 기업 아이덴티티 전면 개편 — 소문자·흙빛 팔레트",
  "펩시코가 약 25년 만에 기업 비주얼 아이덴티티를 전면 개편했다. 소문자 워드마크와 흙빛 컬러 팔레트, 단순화된 아이코노그래피를 도입하고 'P'를 중심에 배치했다.",
  "PepsiCo", "https://www.pepsico.com/newsroom/stories/2026/an-inside-look-at-pepsico-new-visual-identity-with-its-lead-designer"),
 ("BBH, 44년 만의 첫 대규모 리브랜드 — AI 획일화에 맞서 전용 서체",
  "광고 에이전시 BBH가 설립 44년 만에 처음으로 비주얼 아이덴티티를 대대적으로 개편했다. Studio DRAMA와 협업해 창업자들의 필체 개성을 담은 전용 서체를 제작, AI 생성 콘텐츠의 획일화에 맞서 인간적 타이포그래피를 내세웠다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/bbh-studio-drama-rebrand-graphic-design-project-260226"),
 ("Canva '불완전함을 디자인하다' — 2026 비주얼 트렌드 리포트",
  "Canva가 2026년을 '불완전함을 디자인하는 해(Year of Imperfect by Design)'로 명명했다. 손 그림 타이포그래피·유기적 텍스처·표현력 있는 사진이 진정성의 신호로 부각되며, AI 과잉에 맞서는 인간적 감성이 디자인의 주요 흐름으로 자리잡고 있다.",
  "Canva", "https://www.canva.com/newsroom/news/design-trends-2026/"),
 ("구글 워크스페이스 아이콘 전면 교체 유출 — Gemini 계열 'AI 우선' 미학",
  "구글 워크스페이스 앱 아이콘이 'AI 우선' 미학으로 전면 교체되는 정황이 유출됐다. 수년간 이어온 플랫·4색 아이콘 체계를 벗어나 Gemini 계열의 디자인 언어와 결을 맞추는 새 아이콘 시스템으로 전환이 예고된다.",
  "CGfrog", "https://blog.cgfrog.com/new-google-logos-icons-leaked-2026/"),
 ("Creative Boom: '이상함이 브랜딩을 구한다' — 2026 브랜드 생존 전략",
  "Creative Boom이 2026년 브랜딩 생존 키워드로 '이상함(weird)'을 제시했다. AI 제작 콘텐츠가 넘치는 환경에서 독특한 개성과 대담한 실험이 브랜드를 차별화하는 핵심 요소이며, 완벽함보다 개성이 살아남는다고 분석했다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
]

MARKETING = [
 ("구글 검색 68%가 클릭 없이 종료 — SparkToro 2026 제로클릭 보고서",
  "SparkToro의 2026년 연구에 따르면 미국 구글 검색의 68.01%가 클릭 없이 끝난다. 2024년 대비 7.56%포인트 증가했으며, AI 개요(AI Overview) 노출 시 클릭률이 15%에서 8%로 반감됐다. SEO 전략의 근본적 재편이 불가피하다.",
  "Search Engine Land", "https://searchengineland.com/google-zero-click-searches-2026-study-479717"),
 ("키트캣 절도 사건이 2.24억 달러 홍보로 — 칸 라이언즈 PR 그랑프리",
  "이탈리아에서 키트캣 50만 개를 실은 트럭이 도난된 위기를 Burson·VML이 '키트캣 도난 추적기' 캠페인으로 역전시켜 10일 만에 2.24억 달러 미디어 효과를 창출했다. 93개 시장 점유율(SOV) 31%, 참여 수 220만 건으로 2026 칸 라이언즈 PR 그랑프리를 수상했다.",
  "What's Trending", "https://whatstrending.com/kitkat-turned-a-12-tonne-chocolate-heist-into-a-cannes-grand-prix-win/"),
 ("LinkedIn, B2B 크리에이터 마켓플레이스 + BrandWorks 정식 출시",
  "LinkedIn이 6월 10일 B2B 브랜드 전용 크리에이터 마켓플레이스와 BrandWorks를 출시했다. 광고주는 Campaign Manager 안에서 크리에이터를 탐색·협업하고, Thought Leader Ads로 콘텐츠를 부스트할 수 있다. B2B 마케터 82%가 크리에이터가 의사결정자 신뢰도를 높인다고 응답했다.",
  "PPC Land", "https://ppc.land/linkedin-launches-creator-marketplace-and-brandworks-for-b2b-brands/"),
 ("워너브라더스 디스커버리, AWS 에이전틱 AI로 광고 기술 스택 전면 재건",
  "워너브라더스 디스커버리가 AWS와 함께 광고 기획·예측·측정·귀인·주문 관리 전 과정을 에이전틱 AI로 자동화하는 차세대 광고 플랫폼을 구축 중이다. Amazon Bedrock AgentCore 기반으로 Q3 통합 미디어 플래닝을 도입한다.",
  "Warner Bros. Discovery", "https://www.wbd.com/news/warner-bros-discovery-announces-agentic-ai-powered-advertising-technology-built-aws-its"),
 ("구글 마케팅 라이브 2026 — Gemini Ask Advisor로 에이전틱 광고 전환 공식화",
  "5월 20일 구글 마케팅 라이브에서 Gemini 기반 AI 광고 솔루션이 대거 공개됐다. Ask Advisor가 구글 Ads·Analytics·Merchant Center·GMP를 단일 대화형 인터페이스로 통합해 캠페인 기획·실행을 자동화한다.",
  "Google", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("앤트로픽 Claude 광고, 칸 라이언즈 2026 영화 그랑프리 수상",
  "Mother London이 제작한 Anthropic Claude의 슈퍼볼 광고 시리즈가 칸 라이언즈 2026 영화 부문 그랑프리를 수상했다. '몸매 만들기'·'엄마와 소통하기'를 주제로 한 두 편의 광고는 'Ads Are Coming To AI' 캠페인으로 감성적 AI 광고의 새 기준을 세웠다.",
  "Creative Review", "https://www.creativereview.co.uk/cannes-lions-2026-all-the-grand-prix-winners/"),
 ("아디다스 'Original Forever', Oasis 재결합 투어 연계 칸 엔터테인먼트 그랑프리",
  "Johannes Leonardo(뉴욕)가 제작한 아디다스 'Original Forever' 캠페인이 칸 라이언즈 2026 엔터테인먼트 그랑프리를 수상했다. Oasis의 22년 만의 재결합 투어와 연계해 아티스트와 브랜드 모두의 '오리지널' 정신을 대규모로 재현했다.",
  "Cannes Lions", "https://www.canneslions.com/news/final-winners-announced-for-2026"),
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
