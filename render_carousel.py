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
 ("EU AI법 투명성 규정, 8월 2일부터 본격 시행",
  "유럽연합 AI 오피스와 각국 당국이 8월 2일부터 AI법의 투명성 의무 조항을 집행한다. 챗봇 등 AI 시스템은 사람이 아님을 알려야 하고, 딥페이크와 AI 생성 콘텐츠에는 식별 가능한 워터마크를 표시해야 한다.",
  "European Commission", "https://digital-strategy.ec.europa.eu/en/news/commission-starts-enforcing-ai-act-rules-and-new-transparency-requirements-2-august"),
 ("오픈AI·앤스로픽 등 직원 1000여 명, AI 개발 속도 조절 촉구",
  "오픈AI, 앤스로픽, 구글 딥마인드, 메타 등 프런티어 AI 랩 직원 1000명 이상이 'Pacing the Frontier' 공동성명에 서명했다. AI가 스스로 개선하는 속도가 통제 범위를 넘어설 경우에 대비해 국제적인 감속 체계를 마련해야 한다고 촉구했다.",
  "NBC News", "https://www.nbcnews.com/tech/security/openai-anthropic-scientists-ask-us-tools-ai-development-rcna589727"),
 ("구글 어스 AI 이미지 생성 기능, 출시 하루 만에 철회",
  "구글이 어스에 도입한 AI 이미지 생성 기능이 허위정보 확산 우려로 출시 하루 만에 철회됐다. 회사는 정책 위반 이미지가 생성된 사실을 확인했다며 안전장치를 보강할 때까지 기능을 롤백한다고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/07/31/google-nixes-its-earth-ai-feature-one-day-after-launch-amid-criticism-it-would-spread-misinformation/"),
 ("스냅챗, AI로만 만든 영상 스포트라이트 추천에서 제외",
  "스냅챗이 완전히 AI로 생성된 영상을 스포트라이트 추천 대상에서 제외하기로 했다. AI로 보정·편집한 콘텐츠는 계속 추천되지만 실제 사람의 창작물을 우선하겠다는 방침이다.",
  "TechCrunch", "https://techcrunch.com/2026/07/31/snapchat-no-longer-rewards-fully-ai-generated-spotlight-content/"),
 ("유니버설·소니·워너 등 음반사, AI 곡 차트 등재 기준 공동 제안",
  "UMG·소니뮤직·워너뮤직·하이브 등 주요 음반사들이 생성AI로 만든 곡의 차트 등재 기준을 공동 제안했다. 승인된 AI 도구 사용과 저작권 준수, AI 사용 고지, 인간 주도 창작 여부를 조건으로 내걸었지만 어떤 도구가 '승인'된 것인지는 아직 합의되지 않았다.",
  "Billboard", "https://www.billboard.com/pro/umg-wmg-sony-propose-principles-ai-song-chart-eligibility/"),
 ("링크드인, '이거 AI 슬롭 같음' 신고 버튼 도입",
  "링크드인이 이용자가 AI 생성 게시물을 직접 신고할 수 있는 '이거 AI 슬롭 같음' 버튼을 추가했다. 신고된 콘텐츠는 노출이 줄고, 기존 '게시물 향상' AI 작성 기능은 교정 기능으로 대체됐다.",
  "Forbes", "https://www.forbes.com/sites/gabrielalinzainescu/2026/08/01/snapchat-and-linkedin-launch-new-tools-to-curb-ai-slop-in-feeds/"),
 ("유튜버 행크 그린 \"내 AI 사용, 건강하지 않다\" 고백",
  "과학 유튜버 행크 그린이 구독자들에게 AI 챗봇에 대한 자신의 의존도가 건강하지 않은 수준이라고 인정했다. AI 일상화가 개인의 습관에 미치는 영향을 보여주는 사례로 주목받고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/08/01/youtuber-hank-green-says-his-ai-usage-is-not-healthy/"),
]

DESIGN = [
 ("코토, 타이포그래피 파운드리 'CcType' 출범",
  "아마존·구글·넷플릭스 브랜딩을 맡았던 스튜디오 코토가 자체 서체 파운드리 CcType을 출범했다. 첫 서체 'CC Timeline'은 영국 활자와 스위스·독일 모더니즘 사식 서체의 영향을 담은 가변축 디스플레이 세리프체다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/koto-cctype-typography-project-080726"),
 ("NHN링크, 티켓 플랫폼 '티켓링크' 새 로고·슬로건 공개",
  "NHN링크가 티켓 예매 플랫폼 티켓링크를 리브랜딩하며 새 로고와 '나의 취향 자극, 티켓링크' 슬로건을 발표했다. 모바일 앱과 웹 첫 화면 UI도 재구성해 공연·이벤트 정보를 한눈에 보이도록 했다.",
  "뉴스핌", "https://www.newspim.com/news/view/20260701000526"),
 ("포토이즘, 글로벌 확장 겨냥해 새 BI·슬로건 공개",
  "포토 부스 브랜드 포토이즘이 '기록 경험'을 확장한다는 방향으로 새로운 BI와 슬로건을 발표했다. 글로벌·오프라인 영역 확장을 염두에 둔 리브랜딩이다.",
  "아시아경제", "https://www.asiae.co.kr/article/2026070610243405657"),
 ("코카콜라, 전 채널 아우르는 새 디자인 시스템 공개",
  "코카콜라가 패키징·리테일·디지털·모션·사진까지 아우르는 더 표현적이고 통일된 새 디자인 시스템을 선보였다. 브랜드는 이를 '더 코카콜라스러워진' 모습이라고 설명했다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/coca-cola-gets-new-look-that-makes-cola-cola-more-cola-cola"),
 ("데이드림, 패션 브랜드 사이트에 자연어 검색 'AI로 쇼핑' 도입",
  "스타트업 데이드림이 자연어로 원하는 옷을 설명하면 찾아주는 'AI로 쇼핑' 검색을 스토드·앨리스+올리비아 등 브랜드 사이트에 직접 심는 '파워드 바이 데이드림'을 출시했다. 키워드 검색 대신 스타일리스트에게 말하듯 요청하면 결과를 보여주는 방식이다.",
  "Fast Company", "https://www.fastcompany.com/91581145/daydream-ai-shopping-tool-coming-directly-to-your-favorite-brands-website"),
 ("피그마 Config 2026, AI 에이전트·코드 네이티브 디자인 도구 공개",
  "피그마가 연례 콘퍼런스 Config 2026에서 캔버스에 모션·코드 레이어·AI 에이전트를 결합한 신기능을 발표했다. 디자인 레이어를 클릭 한 번으로 코드 레이어로 전환하는 등 디자인 툴을 '지능형 캔버스'로 진화시키겠다는 구상이다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("Brand New, 프리어존스 신서체로 자체 리브랜딩",
  "브랜딩 전문 매체 언더컨시더레이션의 'Brand New'가 프리어존스 타입의 신규 서체 'Community Gothic'을 활용해 자체 로고를 새로 만들었다. 'ra', 'd', 'N' 자모의 리가추어(합자)에서 출발한 디자인이다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_logo_and_website_for_brand_new_by_underconsideration.php"),
]

MARKETING = [
 ("오길비 US CEO 린지 코로나, 취임 1년도 안 돼 사임",
  "린지 코로나가 오길비 미국법인 CEO로 취임한 지 1년이 채 안 돼 자리에서 물러났다. WPP 산하 오길비 글로벌 CEO 로랑 에제키엘이 북미 지역을 임시로 총괄한다.",
  "Ad Age", "https://adage.com/agencies/aa-lyndsey-corona-to-leave-ogilvy/"),
 ("스냅, 에이전시 파트너 프로그램 2단계로 확대 개편",
  "스냅이 에이전시 파트너 프로그램을 신규 진입 단계 '에이전시 파트너'와 기존 '전략 에이전시 파트너'로 나눠 재편했다. 베타 제품 우선 접근, 전담 지원, 할인 등 혜택을 강화해 더 많은 미디어 대행사를 끌어들이려는 전략이다.",
  "Adweek", "https://www.adweek.com/social-marketing/snapchat-expands-agency-partner-program-to-woo-more-media-buyers/"),
 ("레노버 x 베키 지, 월드컵 앞두고 '유어 클럽 유어 캔버스' 캠페인",
  "레노버가 가수 베키 지와 함께 미국·브라질·이탈리아·중국 등 각국 아마추어 축구 클럽 아티스트에게 AI 도구로 유니폼을 디자인할 기회를 주는 캠페인을 시작했다. 유니폼 판매 수익 전액은 참여 클럽에 기부된다.",
  "WebWire", "https://www.webwire.com/ViewPressRel.asp?aId=354158"),
 ("인스타카트, '남성 쇼퍼 밈' 겨냥한 코미디 캠페인 선보여",
  "인스타카트가 '인스타카트 남성 쇼퍼' 밈을 정면으로 활용한 'The Male Shopper Project'를 내부 제작으로 선보였다. 안토니 포로우스키가 엉성한 남성 쇼퍼들을 훈련시키는 조교로 등장한다.",
  "Ad Age", "https://adage.com/creativity/work/aa-instacart-male-shopper-project/"),
 ("고든 램지, 시저스 리퍼블릭 신규 광고에서 레이크 타호 홍보",
  "고든 램지가 등장하는 시저스 리퍼블릭 레이크 타호의 새 광고가 공개됐다. 이탈리아 코모 호수 예찬을 늘어놓다가 실은 타호 호수 보트 위였다는 반전으로, 2억 달러 리모델링을 거친 리조트를 홍보한다.",
  "Ads of the World", "https://www.adsoftheworld.com/campaigns/great-redirect"),
 ("마크 앤써니 브랜즈, UM을 글로벌 미디어 에이전시로 선정",
  "화이트클로·마이크스 하드 레모네이드를 보유한 마크 앤써니 브랜즈가 경쟁 프레젠테이션을 거쳐 옴니콤 산하 UM을 글로벌 미디어 에이전시로 선정했다. 미국·영국·캐나다·호주 시장 전략을 총괄한다.",
  "Mumbrella", "https://mumbrella.com.au/mark-anthony-brands-international-appoints-um-as-global-media-agency-931199"),
 ("프라임 데이 2026, 클릭은 9% 늘고 소비는 45% 줄어",
  "아마존 프라임 데이 클릭 수가 전년 대비 9% 늘었지만 소비자 지출은 45% 감소한 것으로 나타났다. 트래픽과 매출이 반대로 움직이는 현상이 소비자 쇼핑 방식의 근본적 변화를 보여준다는 분석이다.",
  "Adweek", "https://www.adweek.com/adweek-wire/prime-day-data-surprise-9-more-clicks-45-less-consumer-spending-what-changed/"),
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
