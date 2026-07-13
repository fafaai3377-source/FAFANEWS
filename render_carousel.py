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
 ("애플, 오픈AI 상대로 영업비밀 절취 소송 제기",
  "애플이 미국 캘리포니아 북부지방법원에 오픈AI를 상대로 영업비밀 침해 및 계약 위반 소송을 제기했다. 전직 애플 하드웨어 총괄과 엔지니어가 미공개 제품 정보를 유출했으며 이는 오픈AI 경영진 지시에 따른 것이라고 주장했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/10/apple-sues-openai-over-alleged-trade-secret-theft/"),
 ("저커버그 \"메타 AI 아직 결실 못 봐\"… 주가 5% 하락",
  "마크 저커버그 메타 CEO가 사내 타운홀에서 회사의 AI 투자 성과가 아직 결실을 맺지 못했다고 인정했다. 메타는 올해 AI 인프라에 1250억~1450억 달러를 투입할 계획인 가운데 이 발언 이후 주가가 5% 빠졌다.",
  "The Motley Fool", "https://www.fool.com/investing/2026/07/12/mark-zuckerberg-metas-ai-bets-fruition-shares-fell/"),
 ("메타, 에이전트용 신모델 '뮤즈 스파크 1.1' 공개",
  "메타 슈퍼인텔리전스랩스가 도구·컴퓨터 활용과 코딩 능력을 강화한 멀티모달 추론 모델 '뮤즈 스파크 1.1'을 공개했다. 100만 토큰 컨텍스트를 지원하며 신규 메타 모델 API를 통해 퍼블릭 프리뷰로 제공된다.",
  "Meta AI Blog", "https://ai.meta.com/blog/introducing-muse-spark-meta-model-api/"),
 ("xAI·커서, 코딩·에이전트 특화 모델 '그록 4.5' 출시",
  "xAI와 커서가 소프트웨어 엔지니어링을 넘어선 범용 에이전트 작업을 겨냥한 '그록 4.5'를 공동 출시했다. 데스크톱·웹·iOS·CLI·SDK 전 플랫폼에서 즉시 이용 가능하며 출시 첫 주 사용량 한도가 두 배로 제공된다.",
  "Cursor Blog", "https://cursor.com/blog/grok-4-5"),
 ("중국, AI 의인화 서비스 규제 시행 앞두고 바이트댄스·알리바바 기능 중단",
  "중국의 'AI 의인화 상호작용 서비스 관리 잠정 조치'가 7월 15일 발효를 앞두면서 바이트댄스 더우바오와 알리바바 큐원이 개인화된 AI 에이전트·동반자 기능을 순차적으로 끄고 있다. 새 규제는 미성년자 대상 정서적 몰입 유도 AI 서비스를 금지한다.",
  "South China Morning Post", "https://www.scmp.com/tech/big-tech/article/3359482/bytedance-and-alibaba-disable-humanlike-ai-custom-agents-new-rules-loom"),
 ("특허 전문 AI 스타트업 워트인텔리전스, 165억원 시리즈B 유치",
  "특허 데이터 기반 거대언어모델 '플루토LM'을 서비스하는 워트인텔리전스가 알토스벤처스 주도로 165억원 규모 시리즈B 투자를 유치했다. 알바트로스인베스트먼트가 후속 투자자로 참여했다.",
  "이데일리", "https://edaily.co.kr/News/Read?mediaCodeNo=257&newsId=01836806645513536"),
 ("앤트로픽, 코딩 에이전트 '클로드 코워크' 모바일·웹으로 확대",
  "앤트로픽이 데스크톱 전용이던 에이전트 '클로드 코워크'를 웹과 모바일로 확장한다고 밝혔다. Max 요금제 사용자부터 순차 적용되며 출시 기념으로 8월 5일까지 사용량 한도를 두 배로 늘렸다.",
  "TechCrunch", "https://techcrunch.com/2026/07/07/the-coding-agent-wars-are-spilling-into-the-rest-of-the-office-claude-cowork/"),
]

DESIGN = [
 ("피그마 메이크에 GPT-5.6 탑재, 프로토타입 완성도와 속도 개선",
  "피그마가 AI 프로토타이핑 도구 '피그마 메이크'에 오픈AI의 신형 모델 GPT-5.6을 탑재했다고 발표했다. 초기 결과물의 완성도와 반복 작업 속도가 개선됐고 오류 자가복구 기능도 강화됐다.",
  "Figma Blog", "https://www.figma.com/blog/gpt-5-6-is-now-available-in-figma-make/"),
 ("노르딕 여행테크 기업 VISIT, 에센 스튜디오와 통합 브랜드 정체성 공개",
  "북유럽 호스피탈리티 테크 기업 비짓 그룹이 사명을 'VISIT'로 통합하고 디자인 스튜디오 에센과 함께 새 로고·아이덴티티를 공개했다. 호텔·숙박·체험 등 고객사 브랜드를 단일 플랫폼으로 묶어 교차 판매를 지원한다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_visit_by_essen.php"),
 ("스포르팅 CP, 존스 놀스 리치와 120주년 기념 리브랜드 공개",
  "포르투갈 축구 클럽 스포르팅 클루베 드 포르투갈이 창단 120주년을 맞아 브랜딩 에이전시 존스 놀스 리치와 새 엠블럼을 발표했다. 방패 테두리를 걷어내고 사자 문양을 전면에 내세웠으며 전용 서체도 함께 제작했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_sporting_clube_de_portugal_by_jones_knowles_ritchie.php"),
 ("런던 스튜디오 코토, 브랜드 전용 서체 파운드리 'CCType' 론칭",
  "글로벌 크리에이티브 스튜디오 코토가 브랜드 아이덴티티에 최적화된 서체를 만드는 자체 파운드리 CCType을 새로 선보였다. 첫 서체 'CC Timeline'은 영국 금속활자와 스위스 모더니즘을 결합한 디스플레이 세리프체다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/koto-cctype-typography-project-080726"),
 ("재즈클럽 로니 스콧츠, 댄 코트렐 스튜디오와 20여 년 만의 아이덴티티 개편",
  "소호의 전설적 재즈클럽 로니 스콧츠가 2000년대 이후 처음으로 브랜드 아이덴티티를 새단장했다. 디자이너 댄 코트렐은 '레일로드 고딕'과 '센추리 콘덴스드' 두 서체로 재즈 특유의 리듬감을 표현했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/dan-cottrell-studio-ronnie-scotts-branding-graphic-design-project-080726"),
 ("코우쉐드, 어더웨이와 손잡고 서머싯 뿌리 살린 리브랜드 단행",
  "영국 럭셔리 스킨케어 브랜드 코우쉐드가 브랜딩 에이전시 어더웨이와 함께 창립지인 서머싯 지역성을 강조하는 리브랜드를 진행했다. 헬싱키 출신 일러스트레이터의 보태니컬 일러스트를 패키지 전면에 내세웠다.",
  "Creative Boom", "https://www.creativeboom.com/news/cowshed-rebrand-by-otherway/"),
 ("시카고 리벳 & 머신, 'Join with US' 슬로건과 함께 기업 정체성 새단장",
  "미국 조립 부품 제조업체 시카고 리벳 앤 머신이 새 로고와 웹사이트를 포함한 기업 리브랜딩을 발표했다. 개별 부품 제조사에서 종합 조인트 시스템·엔지니어링 솔루션 기업으로의 전환을 알리며 새 슬로건을 내세웠다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/chicago-rivet--machine-co-announces-rebranding-and-new-corporate-identity-302822863.html"),
]

MARKETING = [
 ("치폴레, 'PGA 투어 2K25'와 손잡고 인게임 성과를 실제 리워드로 전환",
  "치폴레가 골프 게임 'PGA 투어 2K25' 안에 테마 퀘스트를 도입해 플레이어 성적에 따라 무료 부리토, 곱빼기 단백질 등 실제 매장 리워드를 지급한다. 게임 내 성과를 실물 보상으로 연결한 첫 사례다.",
  "Marketing Dive", "https://www.marketingdive.com/news/chipotle-drives-rewards-program-with-pga-tour-2k25-integration/824619/"),
 ("굿와입스, LA 거리에 '향기 나는' 옥외광고 설치…감각 마케팅 실험",
  "퍼스널케어 브랜드 굿와입스가 LA 애벗 키니 대로에 30초마다 향을 내뿜는 체험형 옥외광고판을 세웠다. 후각이 감정·기억과 직결된다는 근거를 활용한 '폴리센서리' 브랜딩 트렌드를 반영했다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-goodwipes-new-billboard-campaign-comes-up-smelling-of-roses"),
 ("리복, 신인 스타들과 함께 농구 시장 재도전…'We Rise' 캠페인 공개",
  "리복이 새 캠페인 'We Rise'로 농구 카테고리 복귀를 본격화했다. NBA·WNBA 신인 선수들이 등장하는 흑백 광고로, 지난해 첫 퍼포먼스 농구화 출시에 이은 브랜드 재건 전략의 연장선이다.",
  "Adweek", "https://www.adweek.com/creativity/reebok-ties-its-basketball-comeback-to-a-class-of-rising-stars/"),
 ("TRO x BMW, 굿우드 페스티벌서 '감정 분석' 체험마케팅 측정 시도",
  "옴니콤 산하 체험 마케팅 에이전시 TRO가 굿우드 페스티벌 오브 스피드의 BMW M 체험관에 카메라 기반 감정 분석 기술을 시범 도입했다. 전통 지표를 넘어 실시간 감정 반응으로 체험 효과를 검증하려는 시도다.",
  "micebook", "https://micebook.com/blog/2026/07/10/tro-tests-emotion-analytics-to-measure-bmw-goodwood-activation/"),
 ("웨어러블 기업 웨스프, 전 나이키 CMO 영입",
  "웨어러블 헬스테크 기업 웨스프가 나이키 최고마케팅책임자를 지낸 인물을 신임 CMO로 영입했다. 브랜드 파트너십, 애슬리트 관계, 제품 스토리텔링을 총괄하며 65명 규모의 글로벌 마케팅 조직을 이끈다.",
  "Adweek", "https://www.adweek.com/brand-marketing/whoop-hires-former-nike-cmo-as-its-top-marketer/"),
 ("AI 검색 시대, 브랜드 가시성의 핵심은 '신뢰'",
  "마테크는 AI 어시스턴트가 제품 탐색의 주요 창구로 부상하면서 브랜드가 전통적 검색 순위 최적화를 넘어 AI 답변에 인용·추천되는 것을 목표로 삼아야 한다고 짚었다. 평판 관리와 언드미디어 확보가 새 과제로 떠올랐다.",
  "MarTech", "https://martech.org/why-ai-search-makes-trust-your-most-important-visibility-signal/"),
 ("액티브캠페인, 구글애즈 커넥터 출시…AI로 퍼포먼스 맥스 캠페인 자동화",
  "마케팅 자동화 기업 액티브캠페인이 자사 AI 엔진 '액티브 인텔리전스'에 구글애즈 커넥터를 추가했다. 마케터는 대화형 프롬프트만으로 구글 서치·유튜브·지메일·디스플레이 전반의 캠페인을 생성·운영할 수 있다.",
  "MarTech Series", "https://martechseries.com/mts-insights/staff-writers/activecampaign-launches-google-ads-connector-for-active-intelligence/"),
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
