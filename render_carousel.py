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
 ("다리오 아모데이 \"AI 발전 속도 늦춰야\" — 앤트로픽 3단계 계획 공개",
  "앤트로픽 CEO 다리오 아모데이가 9월 12일 '프런티어 속도 조절'을 주장하는 에세이를 발표했다. 재귀적 자기개선 가속과 오픈AI·허깅페이스 에이전트 스웜 사고를 근거로 들며 제3자 평가기관에 상시 접근권을 부여하겠다고 밝혔다.",
  "Dario Amodei", "https://darioamodei.com/post/we-must-pace-the-frontier"),
 ("OpenAI, GPT-6 Astra 공개 — 사이버보안 '크리티컬' 등급 첫 모델",
  "OpenAI가 9월 3일 GPT-6 Astra를 제한 프리뷰로 공개하고 이튿날 유료 이용자에게 개방했다. 대비 프레임워크상 사이버보안 능력 최고 등급에 처음 도달했으며 컨텍스트는 100만 토큰이다.",
  "Al Jazeera", "https://www.aljazeera.com/economy/2026/9/4/openai-unveils-gpt-6-astra-amid-rising-scrutiny-and-safety"),
 ("OpenAI, 에이전트 API 퍼블릭 베타 출시",
  "9월 10일 OpenAI가 코덱스 하니스를 API 하나로 감싼 에이전트 API를 퍼블릭 베타로 열었다. 세션 오케스트레이션과 컨텍스트 압축을 대신 처리하며 자체·파트너 샌드박스에서 에이전트를 구동할 수 있다.",
  "The Robotics Media", "https://theroboticsmedia.com/article/openai-agents-api-public-beta-codex-harness-september-10-2026"),
 ("세일즈포스, 이름 붙인 에이전트 7종 '에이전트포스' 공개",
  "9월 11일 세일즈포스가 케이시·페이지·카터 등 이름과 직무를 가진 AI 에이전트 7종을 선보였다. 영업·서비스·커머스·IT/HR 등 업무별로 특화됐으며 신규 런타임을 쓰는 헌터는 수주간 파이프라인을 스스로 운영한다.",
  "Startup Fortune", "https://startupfortune.com/salesforce-gives-its-ai-agents-names-job-titles-and-months-of-memory/"),
 ("딥시크, V4.1-Flash 공개 — 플래그십 능가 주장",
  "딥시크가 9월 10일 새 아키텍처 계열의 소형 모델 V4.1-Flash를 오픈 MIT 라이선스로 내놨다. 텍스트·이미지를 함께 처리하는 멀티모달 모델로 자사 플래그십 V4-Pro보다 성능·비용·속도에서 앞선다고 밝혔다.",
  "SiliconANGLE", "https://siliconangle.com/2026/09/10/deepseek-releases-v4-1-flash-says-it-outperforms-flagship-v4-pro/"),
 ("미스트랄, 삼성 주도로 3조원대 투자 유치",
  "프랑스 미스트랄AI가 9월 8일 삼성전자 주도로 30억 유로를 조달해 기업가치를 210억 유로로 끌어올렸다. 유럽 테크기업 사상 최대 규모의 지분 투자로, 자체 데이터센터 구축에 자금을 투입할 계획이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/08/mistral-raises-e3b-as-sovereign-ai-becomes-big-business/"),
 ("코히어, 20조원 밸류에이션으로 대규모 추가 투자 논의",
  "캐나다 AI 스타트업 코히어가 9월 11일 기준 200억 달러 밸류에이션에 20~30억 달러 규모 시리즈E 투자를 협상 중이라고 보도됐다. 성사되면 캐나다 스타트업 사상 최대 민간 투자가 된다.",
  "PYMNTS", "https://www.pymnts.com/startups/2026/ai-startup-cohere-targets-20-billion-dollar-valuation-funding-round"),
]

DESIGN = [
 ("피그마, '생성형 플러그인·셰이더' 정식 공개",
  "피그마 Config 2026에서 발표된 생성형 플러그인·셰이더 기능이 6월 24일부터 순차 적용됐다. 코드 지식 없이 프롬프트만으로 캔버스 전용 도구와 WebGPU 기반 비주얼 이펙트를 만들 수 있다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("피그마 MCP 서버, AI 코딩 에이전트에 디자인 시스템 개방",
  "피그마 MCP 서버가 클로드 코드·커서 등 AI 코딩 도구에 디자인 토큰과 컴포넌트 사양을 직접 전달한다. 디자인-개발 핸드오프 과정의 마찰을 줄이는 것이 목표다.",
  "Figma Help Center", "https://help.figma.com/hc/en-us/articles/32132100833559-Guide-to-the-Figma-MCP-server"),
 ("인스타그램, 10년 만에 워드마크 새 단장",
  "아담 모세리 CEO가 8월 13일 인스타그램 로고 서체 개편을 발표했다. 필기체 골격은 유지하되 's·r·g' 세 글자를 더 둥글게 손으로 그려 넣었고 손글씨체·모노스페이스 서체 패밀리도 함께 선보였다.",
  "Jukebox Print", "https://www.jukeboxprint.com/blog/instagram-new-logo"),
 ("매트릭스, 새 브랜드 아이덴티티 공개",
  "매트릭스가 9월 1일부터 제품·패키지·디지털 채널 전반에 새 비주얼 아이덴티티를 순차 적용하기 시작했다. 8월 중순부터 예고된 개편으로 오프라인 매장과 파트너 접점까지 확대 적용된다.",
  "Business News This Week", "https://businessnewsthisweek.com/business/matrix-unveils-its-new-brand-identity/"),
 ("AI 이미지 플랫폼 아이디오그램, How&How와 새 아이덴티티",
  "아이디오그램이 4.0 오픈웨이트 출시를 계기로 브랜드를 전면 재정비했다. How&How가 만든 새 로고는 뇌 형상에 'I'를 음각으로 새겨 AI 시대에도 사람의 판단이 중심이라는 메시지를 담았다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_ideogram_by_howhow.php"),
 ("포르마판타스마 x 키퍼, 런던디자인페스티벌서 신작 '4' 공개",
  "9월 14일 텍스타일 브랜드 키퍼가 포르마판타스마와 협업한 컬렉션 '4'를 런던디자인페스티벌에서 선보였다. 질감이 다른 실내장식 원단 시리즈로, 패턴이 브랜드 정체성을 구조·움직임·깊이로 풀어낸다.",
  "Wallpaper*", "https://www.wallpaper.com/design-interiors/kieffer-formafantasma-and-rubelli-collaboration"),
 ("브랜드뉴 컨퍼런스 2026, 핑크빛 아이덴티티로 내슈빌 개최 예고",
  "언더컨시더레이션이 주최하는 브랜드뉴 컨퍼런스가 9월 17~18일 내슈빌에서 열린다. 이번 행사 아이덴티티는 배철러렛 파티 콘셉트로 핑크 톤과 스크립트 서체를 활용해 완전히 새로운 방향을 제시했다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnewconference/about_the_identity.php"),
]

MARKETING = [
 ("구글, 검색 캠페인 'AI 맥스' 자동 전환 시작",
  "구글이 9월 1일부터 폭넓은 일치·자동 생성 자산을 쓰는 검색 캠페인을 AI 맥스로 순차 자동 전환하고 있다. 브랜드 포함·제외 설정은 그대로 유지되며 전환은 9월 한 달간 단계적으로 진행된다.",
  "Search Engine Land", "https://searchengineland.com/google-sets-ai-max-migration-timeline-for-search-campaigns-485006"),
 ("마이크로소프트 광고, 'AI 맥스' 전 세계 정식 출시",
  "마이크로소프트 광고가 8월 19일 AI 맥스를 전 세계 모든 광고주 계정에 정식 적용했다. 신규 검색 캠페인에는 기본값으로 켜지며 검색어 매칭·문구 자동생성·최종 URL 확장 기능을 포함한다.",
  "Search Engine Land", "https://searchengineland.com/microsoft-advertising-rolls-out-ai-max-globally-for-search-campaigns-485530"),
 ("어도비, 인도 AI 자동화 스타트업 릴로 인수",
  "어도비가 9월 2일 마케팅 워크플로 자동화 스타트업 릴로를 인수했다고 밝혔다. 자연어 지시만으로 여러 툴에 걸친 캠페인·리드 조사 업무를 대신 처리하는 에이전트 기술을 확보했다.",
  "Business Standard", "https://www.business-standard.com/companies/news/adobe-acquires-indian-ai-automation-startup-rilo-to-boost-agentic-push-126090201601_1.html"),
 ("AI 마테크 시장, 2031년까지 74조원 규모로 3배 성장 전망",
  "최신 보고서는 AI 마테크 시장이 2025년 280억 달러에서 2031년 743억 달러로 커질 것으로 내다봤다. 에이전틱 AI, 플랫폼 통합, 프라이버시 우선 개인화가 성장을 이끄는 핵심 동력으로 꼽혔다.",
  "Complete AI Training", "https://completeaitraining.com/news/global-ai-martech-market-to-nearly-triple-to-743-billion-by/"),
 ("허쉬 x 케이트아이, '핑키 업' 캠페인으로 Z세대 공략",
  "허쉬가 9월 1일 신제품 크렘 바 출시에 맞춰 걸그룹 케이트아이와 캠페인을 시작했다. 지하철을 배경으로 한 60초 광고에 이어 브루클린·LA 옥외광고까지 이어지는 통합 캠페인이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-hersheys-katseye-campaign-takes-on-gen-zs-desire-for-indulgence/829013/"),
 ("구글 애즈, 오프라인 전환 업로드 '7일 규정' 논란",
  "7일이 지나 업로드된 오프라인 전환 데이터는 리포트에는 표시되지만 데이터 기반 어트리뷰션과 스마트 입찰 계산에서는 제외되는 것으로 확인됐다. 같은 데이터인데도 계정마다 전환 수치가 달라지는 원인으로 지목됐다.",
  "PPC Land", "https://ppc.land/google-ads-attribution-ignores-offline-conversions-uploaded-after-7-days/"),
 ("리테일미디어, 타기팅 넘어 '크리에이티브 서비스'로 승부",
  "앨버트슨스·월마트 등 리테일미디어 업체들이 쇼퍼 데이터 타기팅을 넘어 브랜디드 콘텐츠·크리에이티브 제작 역량을 앞세우기 시작했다. 인스타카트도 '애즈 스튜디오'를 신설해 CPG 브랜드와 광고를 공동 제작한다.",
  "Adweek", "https://www.adweek.com/commerce/how-4-retailers-are-pitching-creative-ad-formats/"),
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
