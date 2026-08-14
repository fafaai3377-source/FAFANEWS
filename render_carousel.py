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
 ("OpenAI, GPT-5.6 Sol '울트라패스트' 모드 공개 — 응답속도 14배 향상",
  "OpenAI가 GPT-5.6 Sol 모델에 최대 14배 빠른 속도로 응답하는 '울트라패스트' 모드를 프리뷰로 공개했다. 칩 제조사 세레브라스와 협력해 초당 최대 750개 토큰을 처리하며, 음성·고객지원·금융분석 등 실시간 응답이 중요한 업무에 우선 적용된다.",
  "TechCrunch", "https://techcrunch.com/2026/08/13/openai-introduces-ultrafast-a-new-mode-that-makes-gpt-5-6-sol-work-at-14x-the-speed/"),
 ("앤트로픽, 이스라엘 AI 스타트업 데카트 60억 달러에 인수 협상",
  "앤트로픽이 실시간 영상 생성 및 GPU 효율화 기술을 보유한 이스라엘 스타트업 데카트 인수를 놓고 약 60억 달러 규모의 협상을 진행 중이다. 성사되면 앤트로픽 역사상 최대 규모의 인수합병이 된다.",
  "Fortune", "https://fortune.com/2026/08/13/anthropic-said-in-talks-to-buy-startup-decart-for-6-billion/"),
 ("엔비디아, 5000억 달러 규모 AI 데이터센터 금융 계획 발표",
  "엔비디아가 아폴로, 블랙록, 블랙스톤, 골드만삭스 등 대형 금융사들과 함께 최대 5000억 달러를 AI 데이터센터 구축에 투입하는 계획을 공개했다. 담보 GPU의 감가상각분 최대 25%를 엔비디아가 보증한다.",
  "TechCrunch", "https://techcrunch.com/2026/08/13/nvidias-new-500b-plan-is-risky-but-brilliant-especially-for-aging-gpus/"),
 ("AI 코딩 에이전트 스타트업 코그니션, 400억 달러 밸류에이션 투자 논의",
  "AI 코딩 에이전트 '데빈' 개발사 코그니션이 400억 달러 기업가치로 신규 투자 유치를 논의 중이다. 지난 5월 260억 달러 밸류에이션 투자 유치 3개월 만이며, 연환산 매출 10억 달러가 근거로 제시됐다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/ai-coding-startup-cognition-reportedly-already-in-talks-to-raise-at-40b-valuation/"),
 ("구글 딥마인드, 하사비스 회장직 이동·경영진 대거 이탈로 혼란",
  "구글 딥마인드 CEO 데미스 하사비스가 회장직으로 물러나고 CTO 코레이 카부크추오글루가 일상 경영을 맡게 됐다. 수석과학자 제프 딘 퇴사 등 핵심 인재 유출이 이어지며 제미나이 3.5 프로 출시도 세 차례 연기됐다.",
  "Fortune", "https://fortune.com/2026/08/13/googles-deepmind-is-having-an-identity-crisis/"),
 ("미라지, AI 앵커가 진행하는 세계 최초 24시간 뉴스 채널 시범 방송",
  "영상 생성 AI 기업 미라지가 사람 진행자 없이 AI가 생성한 앵커가 실시간 뉴스를 전하는 방송을 시작했다. 약 5만 달러를 들인 하루짜리 실험 방송으로, 생방송 게시물은 82만 4000회 노출을 기록했다.",
  "Variety", "https://variety.com/2026/digital/news/mirage-ai-generated-24-hour-news-channel-1236834107/"),
 ("하사비스, 백악관·재무부에 'AI판 FINRA' 감독기구 설립 직접 로비",
  "데미스 하사비스가 재무장관과 백악관 과학기술정책국장을 직접 만나 업계 주도의 AI 안전기준 기구 설립을 제안한 것으로 확인됐다. 프론티어 모델을 출시 최대 30일 전 자발적으로 심사받는 방식에서 출발해 향후 의무화하는 방안이 골자다.",
  "Tech Times", "https://www.techtimes.com/articles/324408/20260813/hassabis-lobbied-bessent-kratsios-ai-watchdog-while-treasury-built-its-own.htm"),
]

DESIGN = [
 ("인스타그램, 10년 만의 첫 대대적 브랜드 리프레시 공개",
  "인스타그램이 크리에이티브 디렉터 크리스티 실바 주도로 10년 만의 첫 대규모 브랜드 개편을 발표했다. 기존 스크립트 워드마크는 유지하되 새 서체 '인스타그램 산스'를 도입하고, 지배적이던 선셋 그라데이션 사용은 줄여 사진 특유의 질감을 강화했다.",
  "It's Nice That", "https://www.itsnicethat.com/features/behind-instagrams-first-major-refresh-in-10-years-partnership-130826"),
 ("펜타그램, 폴라 셔 주도로 JETOUR G 시리즈 자동차 비주얼 아이덴티티 공개",
  "펜타그램이 JETOUR G700을 위한 비주얼 아이덴티티 시스템 'Ridge of Steel'을 공개했다. 히말라야 산맥 능선에서 영감을 받은 디자인 언어와 함께 전용 서체·로고를 새로 제작했으며, 폴라 셔가 자동차 브랜드 아이덴티티에 깊이 관여한 첫 프로젝트다.",
  "TopSpeed", "https://www.topspeed.com/limousine-climbs-rocks-jetour-g700-design-advantage/"),
 ("캐나다 빅록 브루어리, 헬름스 워크숍과 브랜드 아이덴티티 전면 개편",
  "캐나다 맥주 브랜드 빅록 브루어리가 디자인 스튜디오 헬름스 워크숍과 협업해 새 로고와 아이덴티티를 공개했다. 대문자 세리프 서체를 새로 도입하고 패키지 전반에 일러스트레이션 요소를 적용했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_big_rock_brewery_by_helms_workshop.php"),
 ("미국 에너지 기업 SCANA, 랜도어와 곰 마스코트 앞세운 새 아이덴티티 공개",
  "미국 유틸리티 기업 SCANA 에너지가 글로벌 디자인 컨설팅사 랜도어와 함께 새 로고와 아이덴티티를 선보였다. 곰 마스코트를 중심으로 자체 제작 전용 서체를 새로 적용했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_scana_energy_by_landor.php"),
 ("소비재 브랜드 어보브 워터, 손그림 감성의 새 패키지 아이덴티티 공개",
  "소비재 브랜드 어보브 워터가 디자인 스튜디오 조 초이 크리에이티브와 협업해 새 로고, 아이덴티티, 패키지 디자인을 공개했다. 손으로 그린 듯한 삐뚤빼뚤한 일러스트레이션으로 유머러스하고 친근한 인상을 강조했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_identity_and_packaging_for_above_water_by_cho_choi_creative.php"),
 ("소프트웨어 기업 암페로스, 스튜디오 투게더와 번개 모티프의 새 워드마크 공개",
  "소프트웨어 브랜드 암페로스가 디자인 스튜디오 투게더와 협업해 새 로고와 아이덴티티를 공개했다. 번개와 콜라주 요소를 활용한 워드마크를 중심으로 브라운 톤 컬러 팔레트를 새로 적용했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_amperos_by_together.php"),
 ("독일 다회용 컵 브랜드 RECUP, 무타보르와 민트색 워드마크로 새 단장",
  "독일의 다회용 컵 공유 서비스 브랜드 RECUP이 디자인 스튜디오 무타보르와 협업해 새로운 아이덴티티를 공개했다. 대문자 워드마크를 민트 계열 색상으로 새로 구성해 브랜드 이미지를 정비했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_recup_by_mutabor.php"),
]

MARKETING = [
 ("마테크 카테고리 최초로 AI에 대체되다 — 경쟁 인텔리전스 툴 위기",
  "Wynter의 2026년 경쟁 인텔리전스 실태 조사에서 B2B 마케터의 21%가 ChatGPT·Claude·Gemini 등 범용 AI를 경쟁사 분석 도구로 활용한다고 답했다. 전용 CI 툴을 꼽은 응답자는 14%에 그쳐 AI 챗봇이 특정 마테크 카테고리를 대체한 첫 사례로 지목됐다.",
  "MarTech", "https://martech.org/heres-the-first-martech-category-replaced-by-ai/"),
 ("쿠이지나트, '숏폼 시대' 겨냥해 14부작 코미디 시리즈 공개",
  "주방가전 브랜드 쿠이지나트가 틱톡·인스타그램 릴스 전용으로 제작한 14부작 코미디 시리즈를 선보였다. 시간 여행을 한 화가와 홈쿡의 일상을 그리며 제품 노출과 유명 푸드 스타일리스트 스타일링을 결합해 짧은 주의 지속 시간의 소비자층을 공략한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/cuisinart-caters-to-thumb-scrolling-consumers-with-short-form-series/827792/"),
 ("파네라 브레드, 카바 출신 CMO 영입하며 브랜드 전환 가속",
  "미국 베이커리 카페 체인 파네라 브레드가 지중해식 레스토랑 카바의 최고마케팅경험책임자였던 앤드루 렙헌을 신임 CMO로 영입했다. 2028년까지 시스템 전체 매출 70억 달러 달성을 목표로 한 전사적 전환 작업의 일환이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/panera-bread-hires-cmo-from-cava-as-transformation-work-continues/827790/"),
 ("작은 아이디어가 큰 성과로 — 하이네켄·몰슨의 캠페인 사례",
  "하이네켄은 폐업 위기의 아일랜드 마을 펍을 주민들이 되사도록 돕는 캠페인으로 2억 3200만 회 노출을 기록하며 올해 크리에이티브 전략 그랑프리를 수상했다. 몰슨은 유니폼 로고 위치만 바꿔 선수 이름을 강조했고, 3주 만에 전국 소매 매출 5.8% 상승으로 이어졌다.",
  "MarTech", "https://martech.org/5-brands-whose-small-actions-drove-big-returns/"),
 ("AI가 애드테크 의사결정을 '블랙박스'로 — 설명 가능성이 새 경쟁력으로",
  "AI 기반 의사결정이 복잡해지면서 프로그래매틱 비디오 플랫폼들이 광고주에게 '왜 그런 결과가 나왔는지'를 설명해야 한다는 압박을 받고 있다. 파편화된 공급망 전반의 예측 능력과 결과 설명력이 새로운 차별화 요소로 부상하고 있다.",
  "MarTech", "https://martech.org/programmatic-video-needs-more-visibility-into-ai-driven-decisions/"),
 ("리퀴드 데스 x 굿와이프스, '소다 향 물티슈'로 월마트 단독 협업",
  "논쟁적 마케팅으로 유명한 생수 브랜드 리퀴드 데스가 위생용품 브랜드 굿와이프스와 손잡고 소다 향 일회용 물티슈를 월마트 단독으로 출시했다. 두 브랜드는 이벤트를 함께 진행하며 유머러스한 마케팅으로 화제를 모았다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-liquid-death-partners-with-goodwipes-in-soda-scented-butt-wipes-collab"),
 ("갭, '옵세션' 스타 기용해 가을 데님 캠페인 공개",
  "갭이 영화 '옵세션'으로 주목받은 인디 나바레트와 싱어송라이터 맬컴 토드를 내세운 가을 데님 캠페인을 공개했다. 백투스쿨 시즌에 맞춰 디지털·소셜미디어·매장·옥외광고 전반에 걸쳐 전개되며 Z세대를 겨냥한 유명인 협업을 이어갔다.",
  "Marketing Dive", "https://www.marketingdive.com/news/gaps-fall-campaign-enlists-obsession-star-to-promote-denim/827375/"),
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
