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
    "나노 바나나 2 라이트": "Nano Banana digital art generator",
    "이미지 생성 모델": "AI image generation digital art",
    "AI 책임 경영 국제표준": "data server certification compliance",
    "ISO 42001": "certification compliance standard",
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
 ("앤트로픽, 가장 에이전틱한 '클로드 소네트 5' 출시",
  "앤트로픽이 추론·도구 사용·코딩 성능을 이전 소네트 4.6 대비 크게 개선한 클로드 소네트 5를 공개했다. 클로드 코드의 기본 모델로 채택되었으며 100만 토큰 네이티브 컨텍스트 윈도우를 제공한다.",
  "Anthropic", "https://www.anthropic.com/news/claude-sonnet-5"),
 ("구글, 초저가·초고속 이미지 생성 모델 '나노 바나나 2 라이트' 공개",
  "구글이 제미나이 3.1 플래시-라이트 이미지를 정식 출시했다. 이미지 1000장당 0.034달러의 저렴한 가격과 4초 내외의 빠른 생성 속도를 내세우며 기업용 저비용·고속 이미지 생성 수요를 겨냥했다.",
  "VentureBeat", "https://venturebeat.com/technology/google-unveils-nano-banana-2-lite-aka-gemini-3-1-flash-lite-for-low-cost-4-second-fast-enterprise-image-generations"),
 ("오픈소스 AI 인프라 기업 투게더AI, 8억 달러 투자로 기업가치 83억 달러",
  "투게더AI가 아람코 벤처스 주도로 8억 달러 규모의 시리즈C 투자를 유치하며 기업가치를 83억 달러로 두 배 이상 끌어올렸다. 오픈소스 모델 기반 저비용 AI 인프라 수요 증가에 힘입어 커서, 코그니션 등 고객사를 확보했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/01/neocloud-together-ai-raises-800m-leaps-to-8-3b-valuation/"),
 ("美 FTC, AI '정확성 왜곡' 규제하는 정책성명 초안 공개",
  "미국 연방거래위원회가 AI 기업이 이념적 목적으로 모델 출력의 정확성을 의도적으로 왜곡할 경우 소비자 기만에 해당할 수 있다는 정책성명 초안을 발표했다. 7월 31일까지 대중 의견을 수렴한다.",
  "Federal Trade Commission", "https://www.ftc.gov/news-events/news/press-releases/2026/07/ftc-seeks-public-comment-policy-statement-addressing-ai-accuracy"),
 ("美 정부, 앤트로픽 최상위 모델 수출 통제 해제",
  "미국 상무부가 지난 6월 국가안보를 이유로 발동했던 앤트로픽 최상위 모델에 대한 수출 통제를 해제했다고 앤트로픽이 밝혔다. 7월 1일부터 클로드닷ai 등 전 세계 서비스에서 접근이 복원되기 시작했다.",
  "Al Jazeera", "https://www.aljazeera.com/economy/2026/7/1/us-lifts-restrictions-on-powerful-ai-models-fable-mythos-anthropic-says"),
 ("코그니션, 취약점 자동 탐지·수정 AI 에이전트 '데빈 시큐리티 스웜' 출시",
  "코그니션이 코드베이스 전반의 취약점을 탐지하고 런타임에서 악용 가능성을 검증한 뒤 수정 PR까지 자동 생성하는 에이전트형 제품을 공개했다. 14개 언어 실제 취약점 벤치마크에서 경쟁 도구보다 높은 탐지율을 기록했다.",
  "Cognition", "https://cognition.com/blog/introducing-devin-security-swarm"),
 ("AI 안전센터, 원격근무 자동화 지수서 역대 최고 기록 발표",
  "AI안전센터가 실제 프리랜서 업무 수행 능력을 측정하는 원격근무 지수에서 앤트로픽 모델이 16.1%의 자동화율로 역대 최고치를 기록해 직전 최고 기록을 두 배 앞질렀다고 밝혔다. 지수 발표 이후 약 8개월 만에 4배 이상 상승했다.",
  "CAIS (Center for AI Safety)", "https://safe.ai/blog/significant-increase-in-digital-labor-automation"),
]

DESIGN = [
 ("삭스 피프스 애비뉴, 110년 백화점 식당가를 새 아이덴티티로",
  "뉴욕 삭스 피프스 애비뉴 백화점이 지하 푸드홀을 'Shaver Hall'이라는 이름으로 재탄생시키며 Love & War 스튜디오가 아이덴티티를 맡았다. 스테이크하우스, 치즈 카운터, 아이스크림 스탠드 등 서로 다른 매장들이 하나의 공간 정체성 안에서 공존하도록 골드리프와 네온 사이니지를 조화롭게 배치했다.",
  "Creative Boom", "https://www.creativeboom.com/news/how-do-you-brand-a-110-year-old-department-store-into-a-food-hall-without-losing-its-soul/"),
 ("바르샤바 포스터 페스티벌, 새로운 그래픽 디자인 세대를 조명하다",
  "폴란드 포스터 스쿨의 역사를 잇는 바르샤바 포스터 페스티벌에서 신진 그래픽 디자이너들의 작업이 소개됐다. 잘 알려지지 않았던 폴란드 그래픽 디자인의 계보와 함께 오늘날 이를 재해석하는 젊은 아티스트들의 시각을 다뤘다.",
  "It's Nice That", "https://www.itsnicethat.com/features/the-poster-festival-warsaw-round-up-graphic-design-spotlight-020726"),
 ("블랑카 도바, 가상 뷰티 브랜드 'Bare Earth' 아이덴티티 공개",
  "디자이너 블랑카 도바가 천연 성분 바디케어 브랜드 컨셉 'Bare Earth'를 위해 워드마크에 그치지 않는 브랜드 세계관을 구축했다. 레이스 테두리, 꽃 사진, 자수 느낌의 일러스트와 캘리그래피 기반 로고로 부드럽고 감성적인 톤을 표현했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/blanca-doba-bare-earth-graphic-design-project-010726"),
 ("D&AD 뉴 블러드 어워드 2026, 생리를 다룬 학생 프로젝트가 최고상 휩쓸어",
  "런던 스틸 야드에서 열린 D&AD 뉴 블러드 어워드 2026 시상식에서 생리를 의료 자산으로 재정의하자는 제안이 그해 최고 영예 두 부문을 모두 수상했다. 화려한 광고 대신 사회적 메시지를 담은 학생 프로젝트가 주목받았다.",
  "Creative Boom", "https://www.creativeboom.com/news/dad-new-blood-awards-2026-a-student-project-about-periods-wins-both-of-the-years-top-prizes/"),
 ("피그마, AI 책임 경영 국제표준 ISO 42001 인증 획득",
  "피그마가 AI 관리 시스템에 대한 국제표준 ISO/IEC 42001 인증을 ANAB 공인 인증기관으로부터 받았다고 발표했다. 문서·정책·리스크 방법론 평가와 38개 통제 항목의 실제 운영을 검증하는 심사를 모두 통과했다.",
  "Figma Blog", "https://www.figma.com/blog/figma-is-now-iso-42001-certified/"),
 ("코토, AI 시대의 스택 오버플로우를 커뮤니티 중심으로 재정의",
  "크리에이티브 스튜디오 코토가 개발자 지식 플랫폼 스택 오버플로우의 브랜드를 AI 시대에 맞게 재편했다. 'Always in build' 컨셉과 전용 서체, 건축 과정에서 착안한 디테일로 커뮤니티 기반 신뢰성을 시각적으로 강조했다.",
  "Creative Boom", "https://www.creativeboom.com/news/koto-reframes-stack-overflow-around-the-one-thing-ai-cant-replace-its-community/"),
 ("APFEL, V&A 이스트 뮤지엄 전시 그래픽에 '만들기'의 정신을 담다",
  "디자인 스튜디오 APFEL이 건축사무소, 아티스트와 협업해 런던 V&A 이스트 뮤지엄의 사이니지와 전시 그래픽을 완성했다. 조명 기구의 모듈형 부품을 스텐실처럼 조합한 전용 서체로 '만들기'라는 전시 주제를 형태 자체에 녹여냈다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/the-act-of-making-is-at-the-heart-of-apfels-exhibition-graphics-for-va-east-museum/"),
]

MARKETING = [
 ("덴츠 크리에이티브, 롯데인디아 크리에이티브 마스터 계정 수주",
  "롯데인디아가 경쟁 피칭 끝에 덴츠 크리에이티브를 창구 대행사로 선정, 초코파이·빼빼로·조이앤토피 라인업의 브랜드 전략과 통합 캠페인을 맡긴다. 인도 내 K-컬처 인기를 활용한 빼빼로 신규 캠페인부터 시작한다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/dentsu-creative-india-wins-lottes-creative-mandate-12125930"),
 ("IBM, 오길비와 30년 관계 끝내고 스택웰을 새 크리에이티브 파트너로 선정",
  "IBM이 경쟁 리뷰를 거쳐 스택웰을 리드 크리에이티브 파트너로 낙점하며 오길비와의 30년 관계를 마감했다. 스택웰 산하 에이전시들이 공동으로 기존 캠페인을 이어가며 8월 신규 캠페인 공개를 앞두고 있다.",
  "MediaPost", "https://www.mediapost.com/publications/article/416216/ibm-taps-stagwell-as-its-new-creative-partner.html"),
 ("허브스팟, AI 구매 인텐트 스타트업 웜리 인수",
  "허브스팟이 웹사이트 방문자의 개인 단위 구매 인텐트를 파악하는 AI 스타트업 웜리를 인수했다. 인바운드 에이전트와 TAM 에이전트를 통해 영업팀의 리드 대응과 아웃바운드 타겟팅을 자동화하며 CRM을 능동형 AI 시스템으로 전환한다.",
  "MarTech", "https://martech.org/hubspots-warmly-deal-points-to-the-next-generation-of-crm/"),
 ("CMO 카운슬, '에이펙스 마테크 매트릭스' 발표",
  "CMO 카운슬이 마텍트라이브와 함께 기업 규모·산업·성숙도·전략 우선순위에 따라 마테크 투자의 비즈니스 가치를 예측하는 프레임워크를 공개했다. 글로벌 마테크 지출이 2150억 달러 규모로 커지는 가운데 스택에서 가치를 끌어내지 못하는 CMO들의 문제를 겨냥했다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/07/01/3320539/0/en/apex-martech-matrix-sets-new-standard-for-turning-martech-into-measurable-performance.html"),
 ("링크드인 활용한 B2B 마케팅과 퍼스널 브랜딩 전략",
  "메텔 이혜환 이사와의 인터뷰를 통해 B2B 기업의 해외 진출을 위한 링크드인 마케팅 전략을 다뤘다. 타깃 고객 정의, 세일즈 목적의 개인 계정 운영, 신뢰 구축을 위한 일관된 콘텐츠 발행이 핵심 성공 요인으로 꼽혔다.",
  "모비인사이드", "https://www.mobiinside.co.kr/2026/07/02/linkedin-b2b-marketing-personal-branding/"),
 ("AI 오버뷰 노출, 일반 검색과 다르다 — 브랜드 가시성 전략",
  "LQ디지털 연구에 따르면 동일 검색어에서 일반 검색 결과에 노출되는 브랜드 인용 중 40% 이상이 AI 오버뷰에는 나타나지 않는 것으로 나타났다. 유튜브 영상이 일반 검색 대비 AI 오버뷰에 4.3배 더 많이 노출돼 영상 콘텐츠 중심의 AI 가시성 전략이 필요함을 시사한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-brands-can-improve-chances-of-showing-up-in-ai-search-overviews/824059/"),
 ("클라우드플레어, 광고 기반 사이트서 AI 학습·에이전트 크롤러 기본 차단",
  "클라우드플레어가 9월 15일부터 광고 기반 웹사이트에서 AI 학습·에이전트 크롤러를 기본 차단하고 검색 색인은 계속 허용하기로 했다. 콘텐츠가 실제 AI 답변에 쓰일 때 수익을 지급하는 '페이 퍼 유즈' 모델도 함께 도입된다.",
  "MediaCopilot", "https://mediacopilot.ai/cloudflare-ai-training-crawlers-default-block/"),
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
    # og:image가 기사 내용과 무관한 것으로 확인된 URL은 강제로 주제 기반 이미지 검색
    MANUAL_FORCE = {
        "https://venturebeat.com/technology/google-unveils-nano-banana-2-lite-aka-gemini-3-1-flash-lite-for-low-cost-4-second-fast-enterprise-image-generations",
        "https://www.figma.com/blog/figma-is-now-iso-42001-certified/",
    }
    def _force(url):
        if url in _seen_urls: return True
        _seen_urls.add(url)
        return url in MANUAL_FORCE
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
