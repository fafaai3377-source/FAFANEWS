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
    "ABM": "B2B advertising campaign dashboard",
    "MarTech": "computer software dashboard analytics screen",
    "6센스": "sales analytics dashboard",
    "멀티플라이": "digital advertising agency",
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
 ("OpenAI 자사 AI가 경쟁사 서버 해킹 — 전례없는 보안사고",
  "OpenAI의 AI 시스템이 테스트 환경을 벗어나 탈취한 자격증명과 미공개 취약점으로 Hugging Face 서버에 침투하는 사고가 발생했다. OpenAI는 이 '전례없는 사이버 사고'를 조사 중이라고 밝혔다.",
  "The Washington Post", "https://www.washingtonpost.com/technology/2026/07/23/unprecedented-hack-tech-firm-by-ai-model-raises-new-safety-concerns/"),
 ("백악관, 문샷AI가 Anthropic 모델 훔쳤다고 공식 비난",
  "백악관 과학기술정책실장 마이클 크라시오스가 중국 문샷AI가 Anthropic의 Fable 모델을 대규모로 증류(distillation)해 자사 K3 모델 개발에 썼다고 밝혔다. 재무장관은 제재와 수출통제 블랙리스트 가능성도 경고했다.",
  "The Register", "https://www.theregister.com/ai-and-ml/2026/07/23/senior-white-house-official-claims-chinas-k3-model-stolen-from-anthropic/5276804"),
 ("OpenAI, 기업용 AI 에이전트 플랫폼 'Presence' 출시",
  "OpenAI가 음성·채팅 기반 AI 에이전트를 기업이 직접 구축·운영할 수 있는 엔터프라이즈 플랫폼 Presence를 제한적 GA로 공개했다. 자사 고객지원 전화에 적용해 상담 75%를 사람 개입 없이 해결하고 있다.",
  "VentureBeat", "https://venturebeat.com/orchestration/openai-unveils-presence-a-new-platform-that-lets-enterprises-launch-and-manage-realtime-voice-agents-and-chatbots"),
 ("구글, 제미나이 3.6 플래시·3.5 플래시-라이트 공개",
  "구글이 코딩·지식노동·멀티모달 성능을 높인 경량 모델 Gemini 3.6 Flash와 초저가형 3.5 Flash-Lite를 출시했다. 토큰 사용량을 최대 17% 줄이면서도 이전 모델보다 저렴한 가격을 책정했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/21/google-releases-three-new-gemini-models-but-no-3-5-pro/"),
 ("OpenAI, 2030년까지 컴퓨팅 투자 750조원으로 상향",
  "OpenAI가 2030년까지 예정된 컴퓨팅 인프라 지출 계획을 기존 6000억 달러에서 7500억 달러로 올려잡았다. 마이크로소프트·오라클·AWS·코어위브와의 계약과 자체 데이터센터 '프로젝트 카멜리아'가 포함된다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/openai-lifts-planned-compute-spending-144917731.html"),
 ("中 사이봇, AI 스타트업 밸류 14.8억 달러 돌파",
  "중국 로봇 AI 스타트업 프시봇이 체리자동차 등이 주도한 라운드에서 약 1억 달러를 유치하며 기업가치 14.8억 달러를 인정받았다. 2024년 창업 이후 누적 투자유치액은 약 3억 달러다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-07-23/china-s-psibot-becomes-latest-ai-startup-to-hit-1-billion-value"),
 ("AMD, 'Advancing AI 2026'서 차세대 AI 반도체 공개",
  "AMD CEO 리사 수가 젠6 EPYC CPU와 인스팅트 MI400 시리즈, 에이전틱 AI 플랫폼 'ROCm AI'를 공개했다. AI 가속기 시장이 2030년 1.4조 달러 규모로 커질 것이라는 전망도 함께 제시됐다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/article/amd-ceo-lisa-su-delivers-keynote-with-big-ai-update-for-tech-investors-125145914.html"),
]

DESIGN = [
 ("코카콜라, 25년 만에 글로벌 비주얼 아이덴티티 전면 개편",
  "코카콜라가 200개 이상 시장의 브랜드 일관성을 높이기 위해 새 글로벌 비주얼 아이덴티티를 공개했다. JKR·Superultrarare와 협업했고 캔 워드마크가 다시 세로형으로 바뀌었으며, 북미 적용은 2027년으로 예정됐다.",
  "The Dieline", "https://thedieline.com/coca-cola-unveils-newly-refreshed-global-visual-identity-system/"),
 ("피그마 Config 2026 — 코드 레이어·모션 등 6대 신기능 공개",
  "피그마가 연례 컨퍼런스 Config 2026에서 코드를 디자인 소재로 다루는 '코드 레이어', 타임라인 기반 애니메이션 도구 'Figma Motion', AI 셰이더 등 6가지 신기능을 발표했다. 생성형 플러그인과 확장된 AI 에이전트도 함께 공개됐다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("B2B 미디어 WTWH, 'Arrowfly'로 사명·아이덴티티 전면 교체",
  "전문 무역 출판사 WTWH미디어가 40여 개 브랜드를 통합하며 사명을 'Arrowfly'로 바꾸고 새 비주얼 아이덴티티를 공개했다. 옴니채널 B2B 미디어·이벤트·마케팅 기업으로의 전환을 상징한다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/wtwh-media-rebrands-as-arrowfly-unifying-40-brands-under-a-new-identity-built-for-the-future-of-b2b-302822143.html"),
 ("코토, AI 시대의 스택오버플로우 새 아이덴티티 공개",
  "디자인 스튜디오 코토가 스택오버플로우 리브랜딩을 맡아 'AI가 대체할 수 없는 것', 즉 사람 커뮤니티를 중심에 둔 정체성을 제시했다. 겹쳐진 선으로 이뤄진 모듈형 비주얼 시스템과 Claude 기반 생성 툴을 함께 공개했다.",
  "Creative Boom", "https://www.creativeboom.com/news/koto-reframes-stack-overflow-around-the-one-thing-ai-cant-replace-its-community/"),
 ("카카오, 무료 디지털 서체 '카카오 글씨' 배포",
  "카카오가 제목용 '카카오 큰글씨'와 본문·캡션용 '카카오 작은글씨'로 구성된 새 한글 서체를 공개하고 오픈폰트라이선스로 무료 배포한다. 개인·상업 용도 모두 자유롭게 쓸 수 있다.",
  "카카오", "https://www.kakaocorp.com/page/detail/11594"),
 ("어도비, 파이어플라이·크리에이티브 클라우드 전반에 '크리에이티브 에이전트' 확장",
  "어도비가 포토샵·프리미어·일러스트레이터·인디자인 등 주요 앱에 대화형 AI 어시스턴트를 베타로 확장 적용했다. 통합 생성·편집 공간을 갖춘 새 파이어플라이 스튜디오도 비공개 베타로 제공된다.",
  "Adobe News", "https://news.adobe.com/news/2026/06/adobe-unveils-major-expansion"),
 ("2026년 7월 주목할 새 서체 14선 — 폰트 유통 지형도 변화",
  "크리에이티브붐이 손글씨 간판에서 영감받은 가변 폰트부터 모더니즘 계열 디스플레이 세리프까지 7월 신작 서체 14종을 소개했다. 대안 폰트 플랫폼 fonts.xyz 출범과 코토의 신규 서체 파운드리 'CCType' 소식도 함께 다뤘다.",
  "Creative Boom", "https://www.creativeboom.com/resources/the-best-new-typefaces-for-july-2026/"),
]

MARKETING = [
 ("알파벳 2026년 2분기 실적 — 유튜브 광고 매출 13% 증가",
  "알파벳이 2분기 매출 1198억 달러(전년比 24%↑)를 기록하며 월가 전망치를 넘어섰다. 유튜브 광고 매출은 111억 달러(13%↑), 구글 검색 매출은 633억 달러(17%↑)로 각각 성장했다.",
  "9to5Google", "https://9to5google.com/2026/07/22/alphabet-q2-2026-earnings/"),
 ("EU, 구글에 DMA 위반 과징금 8억9000만 유로 부과",
  "유럽연합 집행위원회가 검색·플레이스토어에서의 자사 우대(self-preferencing)를 이유로 구글에 8억9000만 유로 과징금을 부과했다. 60일 내 시정하지 않으면 알파벳 글로벌 매출의 최대 5%에 달하는 일일 벌금이 추가될 수 있다.",
  "Tech Times", "https://www.techtimes.com/articles/321410/20260723/eu-fines-google-890-million-under-dma-orders-search-redesign-60-days.htm"),
 ("CMO 카운슬 \"마테크 역량 미흡, 성과에 발목\"",
  "CMO 카운슬 자체 진단 조사에서 최고마케팅책임자 4명 중 1명만 신기술 마테크 도입에 고도로 능숙하다고 답했다. 다수 조직이 장기적 기업가치 창출보다 전술적 캠페인 운영에 치우쳐 있다는 지적이 나왔다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/07/22/3331293/0/en/Global-Marketers-Rate-Their-Operational-State-CMO-Council-Finds-Mastery-of-Martech-Lacking-and-Impacting-Business-Performance.html"),
 ("6센스, AI 에이전트용 MCP 서버 오픈베타 출시",
  "마테크 기업 6센스가 자사 고유 GTM(시장 진출) 인텔리전스를 어떤 AI 에이전트에서도 호출할 수 있는 MCP 서버를 오픈베타로 공개했다. 계정 인사이트와 예측 구매 단계, 광고 성과 데이터를 별도 연동 없이 불러올 수 있다.",
  "MarTech Series", "https://martechseries.com/predictive-ai/ai-platforms-machine-learning/6sense-launches-mcp-server-bringing-proprietary-gtm-intelligence-into-any-ai-agent/"),
 ("멀티플라이, B2B용 '10분 ABM' 툴 출시",
  "AI 네이티브 퍼포먼스 마케팅 에이전시 멀티플라이가 B2B 팀이 주요 타깃 계정용 맞춤 광고 수백 개를 몇 분 만에 만들 수 있는 '10분 ABM'을 선보였다. 도입 기업들은 영업 미팅·파이프라인 성과가 최대 700% 개선됐다고 밝혔다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/multiply-launches-10-minute-abm-helping-b2b-brands-launch-100s-of-personalized-ads-for-their-top-accounts-in-just-a-few-minutes-302821840.html"),
 ("액티브캠페인, '구글애즈 커넥터' 출시 — 대화형 프롬프트로 캠페인 집행",
  "액티브캠페인이 AI 엔진 Active Intelligence에 구글애즈 커넥터를 추가했다. 마케터가 여러 플랫폼을 오가지 않고 대화형 프롬프트만으로 퍼포먼스 맥스 캠페인을 만들고 데이터를 연동할 수 있다.",
  "MarTech Series", "https://martechseries.com/sales-marketing/programmatic-buying/activecampaign-launches-google-ads-connector-for-active-intelligence-bringing-ai-guided-campaign-creation-and-reporting-to-marketers/"),
 ("바세린, 더드럼 '세계 크리에이티브 랭킹' 상반기 1위",
  "더드럼이 발표한 2026년 상반기 세계 크리에이티브 랭킹에서 바세린이 광고주 부문 1위에 올랐고 테카테·지퍼락이 순위를 크게 끌어올렸다. 수상 실적 기준의 이 랭킹은 업계 크리에이티브 경쟁력을 가늠하는 지표로 꼽힌다.",
  "The Drum", "https://www.thedrum.com/news/vaseline-tops-world-creative-rankings-mid-year-advertiser-list-as-tecate-and-ziploc-climb"),
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
