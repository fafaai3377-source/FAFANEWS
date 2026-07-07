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
    "테슬라": "Tesla", "로보택시": "Tesla Robotaxi self-driving car",
    "마이애미": "Miami Florida",
    "미스트랄": "Mistral AI", "오픈AI의": "OpenAI",
    "유엔": "United Nations Geneva", "제네바": "Geneva United Nations",
    "백악관": "White House Washington",
    "제미나이": "Gemini Google AI",
    "폴리마켓": "Polymarket prediction market",
    "버셀": "Vercel", "메르세데스": "Mercedes-AMG Formula One",
    "존 프리다": "John Frieda haircare",
    "덴츠": "Dentsu advertising agency",
    "롯데": "Lotte confectionery snack",
    "스포르팅": "Sporting Clube de Portugal football badge",
    "트리 헛": "Tree Hut body scrub cosmetics",
    "슈퍼걸": "Supergirl movie poster",
    "워크스페이스": "Google Workspace icons",
    "구글 애즈": "Google Ads advertising",
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
 ("Anthropic, 연 매출 300억 달러 돌파",
  "Anthropic의 연환산 매출(run rate)이 300억 달러를 넘어섰다. 분기 성장률 80배를 기록하며 세일즈포스가 20년 걸린 매출 규모를 3년 만에 달성했다.",
  "VentureBeat", "https://venturebeat.com/technology/anthropic-says-it-hit-a-30-billion-revenue-run-rate-after-crazy-80x-growth"),
 ("OpenAI, GPT-5.6 Sol·Terra·Luna 프리뷰 공개",
  "OpenAI가 차세대 모델 3종 GPT-5.6 Sol·Terra·Luna를 제한된 파트너 대상 프리뷰로 공개했다. Sol은 고난도 코딩·보안 연구용, Terra는 대량 업무용, Luna는 초고속 저비용 작업용이다.",
  "OpenAI", "https://openai.com/index/previewing-gpt-5-6-sol/"),
 ("구글 Gemini 3.5 Pro, 200만 토큰 컨텍스트로 프리뷰 중",
  "구글의 차세대 플래그십 모델 Gemini 3.5 Pro가 버텍스 AI 엔터프라이즈 프리뷰 2주차에 접어들었다. 프로덕션 프런티어 모델 중 최대인 200만 토큰 컨텍스트와 딥싱크 모드를 지원한다.",
  "The AI Rankings", "https://theairankings.com/google/gemini-3-5-pro/"),
 ("UN, 첫 AI 거버넌스 글로벌 대화 제네바서 개막",
  "유엔 193개 회원국과 민간·시민사회가 참여하는 첫 AI 거버넌스 글로벌 대화가 제네바에서 7월 6~7일 열렸다. AI 혜택의 공평한 배분과 국제 협력 방안을 논의했다.",
  "UN News", "https://news.un.org/en/story/2026/07/1167862"),
 ("백악관, 프런티어 AI 모델 자율 검토 행정명령 서명",
  "백악관이 프런티어 AI 모델의 자율적 사전 검토 절차를 담은 행정명령에 서명했다. 개발사가 정부와 협의해 공개 전 최대 30일간 모델 접근을 허용하는 자율 체계를 만든다.",
  "The White House", "https://www.whitehouse.gov/presidential-actions/2026/06/promoting-advanced-artificial-intelligence-innovation-and-security/"),
 ("테슬라 로보택시, 안전요원 없이 마이애미 상륙",
  "테슬라가 마이애미에서 안전요원 동승 없이 완전 무인 로보택시 서비스를 시작했다. 텍사스 밖 첫 도시이자, 처음부터 안전요원 없이 출범한 첫 사례다.",
  "Engadget", "https://www.engadget.com/2207974/tesla-expands-robotaxi-service-to-small-section-of-miami/"),
 ("미스트랄, 이번 여름 오픈웨이트 신모델 예고",
  "OpenAI의 대항마로 꼽히는 프랑스 미스트랄이 이번 여름 새 오픈웨이트 모델을 출시하며 7월 중 얼리 액세스를 연다고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/07/04/what-is-mistral-ai-everything-to-know-about-the-openai-competitor/"),
]

DESIGN = [
 ("스포르팅CP, 창단 120주년 맞아 엠블럼 리브랜드",
  "포르투갈 스포르팅 클루베 드 포르투갈이 JKR과 함께 엠블럼을 개편했다. 방패를 걷어내고 사자를 전면에 내세웠으며, 폰트 파운드리 F37과 전용 서체 'Sporting Sans'를 만들었다.",
  "Dezeen", "https://www.dezeen.com/2026/07/06/sporting-cp-club-badge-logo-rebrand/"),
 ("피그마 Config 2026 — 모션·코드 레이어 공개",
  "피그마가 연례 컨퍼런스 Config 2026에서 애니메이션 툴 '모션', 코드 레이어, AI 셰이더 등을 발표했다. 코드 레이어 얼리 액세스는 7월부터 시작된다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("트리 헛, 'Uncontain Yourself' 브랜드 아이덴티티 공개",
  "바디스크럽 브랜드 트리 헛이 로고·패키지·웹사이트를 아우르는 전면 리브랜드를 공개했다. 시그니처 슈가스크럽의 질감에서 영감을 받은 새 로고로 자신감과 유희를 표현했다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/a-glow-up-for-tree-hut-encouraging-consumers-to-uncontain-yourself-302648977.html"),
 ("구글 워크스페이스 아이콘, 'AI 퍼스트'로 전면 개편 정황",
  "수년간 이어진 플랫 4색 아이콘 체계를 벗어나 Gemini 계열과 결을 맞춘 새 워크스페이스 아이콘이 유출됐다. AI 중심 미학으로의 전환이 예상된다.",
  "CGfrog", "https://blog.cgfrog.com/new-google-logos-icons-leaked-2026/"),
 ("디자인 툴 삼국지 — 피그마·어도비·캔바 '프레너미' 시대",
  "Anthropic이 독립 디자인 툴 'Claude Design'을 내놓으며 피그마·어도비·캔바 간 경쟁 구도가 복잡해졌다. 서로 파트너이자 경쟁자인 관계가 심화되고 있다.",
  "Fast Company", "https://www.fastcompany.com/91538439/design-enters-its-frenemies-era"),
 ("메트로 애틀랜틱 애슬레틱 콘퍼런스, 아이덴티티 리브랜드",
  "미국 대학스포츠 콘퍼런스 MAAC이 정체성 강화를 위한 리브랜드를 단행했다. 소속 대학들의 결속력을 높이는 새 비주얼 시스템을 도입했다.",
  "NJBIZ", "https://njbiz.com/metro-conference-rebrand-maac-identity/"),
 ("브랜드 임팩트 어워즈 2026, 출품 접수 마감 임박",
  "13회째를 맞는 브랜드 임팩트 어워즈가 전 세계 브랜딩 프로젝트 출품을 받고 있다. 접수 마감은 7월 9일로, 업종별 특성을 고려해 심사한다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/the-brand-impact-awards-2026-are-officially-open-for-entries"),
]

MARKETING = [
 ("버셀-메르세데스 AMG 페트로나스 F1, 다년 파트너십",
  "클라우드 플랫폼 버셀이 메르세데스 AMG 페트로나스 F1팀과 다년 전략적 파트너십을 맺었다. 7월 2~5일 영국 그랑프리부터 레이스카에 버셀 브랜드가 노출된다.",
  "Businesswire", "https://www.businesswire.com/news/home/20260701960073/en/Vercel-and-Mercedes-AMG-PETRONAS-Formula-One-Team-Announce-Multi-Year-Strategic-Partnership"),
 ("구글 애즈, 구식 어트리뷰션 모델 4종 폐지",
  "구글이 7월 중순부터 첫 클릭·선형·시간가치감소·위치기반 어트리뷰션 모델 선택을 없앤다. 전환율 3% 미만 채택률을 이유로 데이터 기반 어트리뷰션으로 자동 전환한다.",
  "Google Ads Help", "https://support.google.com/google-ads/announcements/9048695?hl=en"),
 ("존 프리다, VCCP와 '살롱 애티튜드' 글로벌 캠페인",
  "헤어케어 브랜드 존 프리다가 VCCP와 손잡고 'Salon Attitude. Every Day.' 캠페인을 공개했다. 복잡한 과학 대신 매일의 자신감과 변화를 강조하는 방향으로 전환했다.",
  "VCCP", "https://www.vccp.com/news/2026/01/john-frieda-makes-a-bold-global-return-with-new-brand-platform-from-vccp/"),
 ("덴츠 크리에이티브 인디아, 롯데 페레로 캠페인 수주",
  "덴츠 크리에이티브 인디아가 다수 대행사 경쟁 끝에 롯데 인디아의 크리에이티브 마운데이트를 따냈다. 초코파이·빼빼로 등 브랜드의 새 캠페인을 이끈다.",
  "LBBOnline", "https://lbbonline.com/news/Dentsu-Creative-India-Bags-Lottes-Creative-Mandate"),
 ("메타, 스레드·릴스 광고 포맷 확대",
  "메타가 스레드와 릴스에 캐러셀·포스트뷰 광고 등 새 포맷을 추가했다. 스레드는 4억 명 이상의 월간 활성 사용자를 기반으로 광고 상품을 넓히고 있다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/meta-adds-more-reels-and-threads-ad-options/759946/"),
 ("폴리마켓, 첫 브랜드 캠페인 'Questions Are Everything' 공개",
  "예측 시장 플랫폼 폴리마켓이 릭 루빈·퓨처·페소 플루마와 함께 데뷔 브랜드 캠페인을 월드컵 기간에 맞춰 선보였다. 질문과 시장, 실시간 여론을 브랜드 언어로 녹였다.",
  "Roastbrief", "https://roastbrief.us/polymarkets-debut-brand-campaign-launches-during-world-cup-rick-rubin-future-peso-pluma/"),
 ("'슈퍼걸' 개봉 앞두고 1억 달러 규모 브랜드 파트너십 캠페인",
  "DC 영화 '슈퍼걸' 개봉을 앞두고 다수 브랜드가 참여하는 1억 달러 이상 규모의 광고 파트너십 캠페인이 전개되고 있다. 역대 최대 수준의 브랜드 협업 마케팅으로 주목받는다.",
  "Deadline", "https://deadline.com/2026/06/supergirl-brand-partners-ad-campaign-1236950265/"),
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
