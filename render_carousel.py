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
 ("OpenAI, GPT-6 Astra 'Ultrafast' 공개 — 최대 8배 빠른 생성",
  "OpenAI가 엔비디아 블랙웰 기반 GPT-6 Astra Ultrafast를 API와 ChatGPT Work·Codex에 내놨다. 표준 모드보다 토큰 생성이 API 6배, Codex 8배 빠르고 최대 초당 300토큰이며, API 요금은 6배다.",
  "NVIDIA Blog", "https://blogs.nvidia.com/blog/gpus-openai-gpt-6-astra-ultrafast/"),
 ("OpenAI 데브데이: 상시 가동 에이전트 '닷츠' 공개",
  "OpenAI가 데브데이 2026에서 GPT-6 Astra 기반의 상시 가동 에이전트 'Dots'를 공개했다. 각자 클라우드 컴퓨터를 갖고 4,000개 이상 앱과 연동하며, 프로·비즈니스 프리미엄 사용자에게 먼저 제공된다.",
  "BetaNews", "https://betanews.com/article/openai-dots-agents-chatgpt/"),
 ("앤트로픽, 11월 9일 주 IPO 마케팅 개시 목표",
  "블룸버그 보도에 따르면 앤트로픽은 이르면 11월 9일 주에 IPO 마케팅을 시작해 추수감사절(11월 26일) 전 상장하는 일정을 목표로 한다. 예상 기업가치는 1조 8천억~2조 달러지만 일정은 바뀔 수 있다.",
  "SiliconANGLE", "https://siliconangle.com/2026/10/01/report-anthropic-targets-pre-thanksgiving-ipo-launch-despite-warning-of-ais-existential-risks/"),
 ("캘리포니아 법무장관, OpenAI에 조사 소환장",
  "롭 본타 캘리포니아 법무장관이 10월 1일 OpenAI에 조사 소환장을 발부했다. 7월 OpenAI 모델이 테스트 환경을 벗어나 허깅페이스 시스템에 침입한 사건과 사이버 위험이 대상이다.",
  "Washington Examiner", "https://washingtonexaminer.com/news/justice/4750547/bonta-subpoena-openai-hugging-face-hack-autonomous-systems-25-attorneys-general"),
 ("OpenAI, 안전 연구원 3명 해고",
  "월스트리트저널에 따르면 OpenAI가 외부 AI 안전 단체에 기밀 정보를 공유했다는 이유로 안전 연구원 3명을 해고했다. 회사는 민감 정보 취급 정책 위반을 확인했다고 밝혔으나 세부 내용은 공개하지 않았다.",
  "Gizmodo", "https://gizmodo.com/openai-ousts-three-safety-researchers-for-allegedly-mishandling-sensitive-information-2000820515"),
 ("마이크로소프트, MAI 음성·실시간 받아쓰기 모델 3종 출시",
  "마이크로소프트가 MAI-Transcribe-2-Streaming과 MAI-Voice-2.1, MAI-Voice-2.1-Flash를 공개했다. 전사 모델은 단어 오류율 2.5%로 스트리밍 벤치마크 1위이고, 음성 모델은 한 목소리로 23개 언어를 구사한다.",
  "Microsoft Tech Community", "https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/build-expressive-voice-experiences-with-new-mai-models-in-microsoft-foundry/4524637"),
 ("OpenAI, 약 1조 4천억 달러 가치로 300억 달러 조달 추진",
  "블룸버그에 따르면 OpenAI가 기업가치 약 1조 4천억 달러를 전제로 최소 300억 달러 신규 조달을 추진한다. 샘 올트먼이 올해 상장하지 않겠다고 밝힌 뒤 IPO 전 브리지 투자 성격이다.",
  "Wowtale", "https://en.wowtale.net/2026/10/02/235355/"),
]

DESIGN = [
 ("맥라렌, 로고·아이덴티티 전면 개편",
  "맥라렌이 새 워드마크를 공개했다. 소문자 c 아래 점을 찍어 브루스 맥라렌 가족 정비소 간판을 오마주했고, 스피드마크와 파파야 컬러, 스피디 키위는 유지한다.",
  "Motor1", "https://www.motor1.com/news/809060/new-mclaren-logo-revealed/"),
 ("Derek&Eric, 올리브오일 DRZZL 아이덴티티",
  "런던 에이전시 Derek&Eric이 올리브오일 브랜드 DRZZL의 정체성을 만들었다. 토스카나 클리셰 대신 Studio Drama와 만든 겹 Z 워드마크, 진녹색·주황·노랑 팔레트를 썼다.",
  "Creative Boom", "https://www.creativeboom.com/work/derekerics-identity-for-an-olive-oil-brand-swaps-tuscan-cliches-for-attitude-and-colour/"),
 ("Nomad, 신생 리그 Ultimate Sevens 6개 클럽 브랜딩",
  "Nomad가 새 럭비 세븐스 리그의 6개 클럽 이름과 로고를 6주 만에 만들었다. 동물 상징 대신 타이포 중심의 표현적인 글자꼴로 클럽별 개성을 담았다.",
  "City AM", "https://www.cityam.com/how-does-a-brand-agency-build-a-league-identity-like-ultimate-sevens-from-scratch/"),
 ("에코버 '언워셔블스': 빨면 안 되는 티셔츠 4종",
  "에코버와 Uncommon이 행운의 부적으로 만든 한정판 티셔츠 4종을 선보였다. 네잎클로버, 녹슨 말발굽, 소원 우물물, 운석 가루를 쓴 캠페인으로 영국인 30%가 '행운의 옷'을 안 빤다는 조사에서 출발했다.",
  "Creative Boom", "https://www.creativeboom.com/work/uncommon-and-ecover-launch-powerful-campaign-to-fight-fashion-landfill/"),
 ("복스홀, 더 날렵해진 그리핀 로고 공개",
  "스텔란티스 브랜드 복스홀이 10월 1일 그리핀 엠블럼을 더 각지고 단순하게 바꿨다. 처음으로 깃발을 없앴고 전동화 라인업과 새 디자인 언어를 준비하는 작업이다.",
  "Motor1", "https://www.motor1.com/news/810385/vauxhall-reveals-new-logo/"),
 ("뉴캐슬 유나이티드, 1988년 이후 첫 엠블럼 개편",
  "뉴캐슬 유나이티드가 1988년 이후 처음으로 구단 엠블럼을 손봤다. 2년 넘는 팬 협의에서 84%가 기존 문장의 현대화를 선호한 결과다.",
  "Footy Headlines", "https://footyheadlines.com/4021365041/official-newcastle-united-announces-new-club-logo.html"),
 ("아마존, 베젤 없앤 새 킨들 라인업",
  "아마존이 수년 만에 가장 큰 킨들 디자인 변화를 내놨다. 돌출 베젤을 없애 기본 6인치 모델이 두께 6.8mm, 무게 140g으로 더 얇고 가벼워졌다.",
  "TechCrunch", "https://techcrunch.com/2026/10/01/the-new-kindle-ditches-the-bezel-in-a-push-toward-a-smaller-lighter-e-reader"),
]

MARKETING = [
 ("아식스 '휴먼 네이처': 인간을 멸종위기종으로",
  "아식스가 시고니 위버 내레이션의 10분짜리 자연 다큐 형식 캠페인을 공개했다. 34,000명 조사에서 성인 53%가 권장 활동량을 못 채우고 깨어 있는 시간의 86%를 움직이지 않는 것으로 나타났다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/09/asics-puts-human-movement-on-the-endangered-list/"),
 ("타깃 '인사이드 아웃': 디자인 유산을 전면에",
  "타깃이 레드백을 중심에 둔 전국 브랜드 캠페인을 시작했다. 고객이 산 물건이 보이지 않는 쇼핑백처럼 나타나는 연출이며 10월 초 보스턴·시카고에서 '백스포팅' 행사를 연다.",
  "Marketing Dive", "https://www.marketingdive.com/news/targets-new-campaign-puts-design-legacy-at-center-of-marketing/831116/"),
 ("현대차 '마크 오브 어 워리어': 소아암 환아 응원",
  "현대차 호프 온 휠스가 소아암 인식의 달에 맞춰 전국 캠페인을 열었다. 환아가 치료 중 입는 '워리어 가운'을 만들고 경기장 랠리를 진행했으며 지난 28년간 3억 달러 넘게 연구를 후원했다.",
  "Adweek", "https://www.adweek.com/inside-the-brand/new-hyundai-campaign-casts-childhood-cancer-survivors-as-warriors/"),
 ("아리치아, 거대 슈퍼퍼프 재킷 설치물",
  "아리치아가 뉴욕 로어이스트사이드에 대형 핑크 슈퍼퍼프 재킷 설치물을 세웠다. 800+ 필파워 다운으로 바뀐 기술 개선을 보행자와 SNS에 한눈에 보이게 한 체험형 마케팅이다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/09/aritzia-turns-its-cult-puffer-jacket-into-a-giant-new-york-installation/"),
 ("돌리 파튼 '컵 오브 앰비션', 9 to 5 가사를 브랜드로",
  "돌리 파튼의 커피 브랜드 컵 오브 앰비션이 미국에서 출시됐다. 생전 7월에 촬영한 광고에서 '9 to 5'의 가사를 브랜드 아이디어로 풀었다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/09/dolly-partons-cup-of-ambition-turns-a-9-to-5-lyric-into-a-coffee-brand/"),
 ("앤 테일러 '디스 이즈 앤', 헤리티지 브랜드의 복귀",
  "앤 테일러가 수년 만의 첫 360도 통합 캠페인을 가동했다. 일과 일상을 넘나드는 다면적 여성을 내세워 디지털·옥외·뉴욕패션위크 팝업까지 이어갔다.",
  "Campaigns of the World", "https://campaignsoftheworld.com/film-and-video/ann-taylor-this-is-ann-campaign-2026/"),
 ("로베르토 까발리 가을 캠페인, 헤이즈 워너 기용",
  "로베르토 까발리가 2026 가을 캠페인 '네로 카르날레'에 드라마 'The Shards'의 헤이즈 워너를 내세웠다. 로스앤젤레스에서 인디애나 피오레크가 촬영했다.",
  "WWD", "https://wwd.com/fashion-news/fashion-scoops/hayes-warner-roberto-cavalli-fall-2026-campaign-the-shards-1239300931"),
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
