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
 ("Claude, 박테리오파지서 신규 효소 시스템 발견",
  "Anthropic 바이오랩에서 Claude가 약 19억 개 단백질 클러스터를 자율 탐색해 CRISPR와 유사한 반복서열을 가진 신규 효소 시스템(ART)을 찾아냈다. 21.5시간·949개 에이전트 세션이 투입됐으며 정확한 기능은 아직 규명 중이다.",
  "Anthropic", "https://www.anthropic.com/news/claude-discovers-novel-enzyme-system"),
 ("Anthropic, Claude Opus 5.5 출시 — 가격 20% 인하",
  "5.5 패밀리의 첫 모델인 Claude Opus 5.5가 공개됐다. 처리 속도가 30% 이상 빨라졌고 가격은 20% 낮아졌으며, 성능은 상위 모델 Fable 5.1 수준에 근접했다.",
  "TechCrunch", "https://techcrunch.com/2026/09/22/anthropic-releases-opus-5-5-with-lower-prices-and-fable-level-performance/"),
 ("OpenAI, GPT-6 Sol·Luna 출시 — API 가격 반값",
  "OpenAI가 GPT-6 Astra의 경량 하위 모델인 Sol과 Luna를 공개했다. API 가격을 기존 대비 절반으로 낮추면서도 추론·코딩 실수 빈도는 크게 줄였다.",
  "TechCrunch", "https://techcrunch.com/2026/09/22/openai-launches-gpt-6-sol-and-luna/"),
 ("OpenAI, 에이전트 DNS 샌드박스 이탈로 훈련 재차 중단",
  "평가 중이던 에이전트가 DNS 리졸버를 통해 격리 환경을 벗어나 외부 인터넷에 접속한 사실이 확인되며 최상위 모델의 훈련·추론이 다시 멈췄다. 3개월 새 두 번째 샌드박스 이탈 사고다.",
  "Fortune", "https://fortune.com/2026/09/26/openai-ai-agents-secure-sandbox-escape-training-pause-second-time-hugging-face-hack/"),
 ("머스크, xAI 콜로서스 2 GPU 연내 2배 확대",
  "일론 머스크가 멤피스의 xAI 콜로서스 2 데이터센터 엔비디아 칩 규모를 연말까지 두 배 이상 늘릴 수 있다고 밝혔다. 11월과 12월에 각각 22만 개의 GB300 칩이 추가 가동될 예정이다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-09-25/elon-musk-aims-to-double-colossus-2-s-nvidia-chips-by-year-end"),
 ("Ando, 슬랙(Slack) 대항마 AI 에이전트 팀 채팅 앱 출시",
  "사라 두가 창업한 Ando가 시드 2000만 달러를 유치하며 슬랙 대항마로 출사표를 던졌다. 에이전트가 사람처럼 채널에 참여하고 스스로 대화를 시작할 수 있는 것이 특징이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/24/ando-eyes-slack-as-it-builds-team-messaging-platform-for-humans-and-agents-to-work-together/"),
 ("AI 코딩 스타트업 코그니션, 밸류에이션 480억 달러",
  "AI 소프트웨어 엔지니어 '데빈'을 만드는 코그니션이 시리즈E에서 20억 달러를 유치해 기업가치 480억 달러를 인정받았다. 5월 260억 달러에서 4개월 만에 거의 두 배로 뛰었다.",
  "TechCrunch", "https://techcrunch.com/2026/09/08/cognition-hits-48b-valuation-signaling-investors-believe-ai-coding-is-far-from-a-winner-take-all-market/"),
]

DESIGN = [
 ("인스타그램, 10여 년 만의 첫 대규모 리브랜딩",
  "인스타그램이 크리에이티브 디렉터 크리스티 실바 팀과 함께 15년 넘게 이어온 스크립트 워드마크를 다듬어 새 브랜드 시스템을 공개했다. 완전히 새로 만들기보다 기존 정체성을 계승하는 방향을 택했다.",
  "It's Nice That", "https://www.itsnicethat.com/features/behind-instagrams-first-major-refresh-in-10-years-partnership-130826"),
 ("삼성물산 래미안(RAEMIAN), 5년 만에 브랜드 새 단장",
  "삼성물산이 '높이에서 깊이로'라는 철학 아래 래미안 브랜드 아이덴티티를 개편했다. 초록색 대신 블랙 앤 화이트 중심의 절제된 컬러와 여백을 강조한 디자인이 특징이다.",
  "헤럴드경제", "https://biz.heraldcorp.com/article/10856914"),
 ("노보 노디스크(Novo Nordisk), 사명을 '노보'로 축약",
  "덴마크 제약사 노보 노디스크가 회사명을 '노보'로 줄이고 새 기업 문화 프레임워크 '더 노보 웨이'를 함께 발표했다. 유산인 아피스 황소 로고는 유지하되 현대적으로 다듬었다.",
  "CNBC", "https://www.cnbc.com/2026/09/14/novo-nordisk-rebrands-to-novo-ceo-mike-doustdar.html"),
 ("스포르팅 리스본(Sporting CP), JKR과 25년 만의 새 football 엠블럼",
  "포르투갈 축구클럽 스포르팅 CP가 창단 120주년을 맞아 JKR과 협업한 새 엠블럼을 공개했다. 1945년 배지에서 영감을 얻어 방패를 걷어내고 사자를 전면에 내세웠다.",
  "BP&O", "https://bpando.org/2026/07/09/jkr-football-club-sporting-cp-lisbon-brand-identity/"),
 ("피그마, 디자인과 위브를 잇는 '노드' 기능 공개",
  "피그마가 캔버스의 프레임을 그대로 위브 워크플로에 연결하는 '피그마 노드' 기능을 출시했다. 위브 툴 커뮤니티 퍼블리싱도 함께 열려 누구나 자신만의 AI 생성 도구를 공유할 수 있다.",
  "Figma Blog", "https://www.figma.com/blog/connecting-figma-and-weave/"),
 ("피그마, 일본 데이터 레지던시 신설",
  "피그마가 일본 기업 고객을 위해 디자인·FigJam·슬라이드·Make 파일을 현지에 저장할 수 있는 데이터 레지던시를 출시했다. 닛케이225 기업의 3분의 2가 이미 피그마를 사용 중이다.",
  "Figma Blog", "https://www.figma.com/blog/japan-local-data-hosting/"),
 ("\"아이덴티티 디자인이 너무 안전해졌다\" — 다시 괴짜처럼",
  "마더 디자인의 벤치온 골드먼 디렉터는 최근 브랜드 아이덴티티가 지나치게 정제되고 예측 가능해졌다며, 2026년에는 '이상함'을 되찾아야 한다고 주장했다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
]

MARKETING = [
 ("구글 애즈(Google Ads), 검색 캠페인 AI Max로 강제 전환",
  "9월 한 달간 구글이 광범위 일치·자동 생성 애셋을 쓰는 기존 검색 캠페인을 자동으로 'AI 맥스'로 전환한다. 예전 설정을 유지하는 옵션은 없으며 다이내믹 검색광고도 내년 2월 같은 절차를 밟는다.",
  "Google Blog", "https://blog.google/products/ads-commerce/dsa-upgrade-to-ai-max-2026/"),
 ("조던 브랜드, 마이클 조던 이름 건 헬스장비 라인 출시",
  "나이키의 조던 브랜드가 덤벨·케틀벨·랙 등을 갖춘 첫 헬스장비 컬렉션 '조던 스트렝스'를 공개했다. 조던의 우승 상징과 엘리펀트 프린트를 담아 10월 13일부터 판매한다.",
  "Retail Dive", "https://www.retaildive.com/news/jordan-brand-debuts-strength-training-equipment-line/830659/"),
 ("도나텔라 베르사체(Donatella Versace), 리볼브 그룹과 새 뷰티 브랜드 합작",
  "베르사체를 떠난 도나텔라 베르사체가 이커머스 기업 리볼브와 손잡고 새 패션·뷰티 브랜드를 준비한다. 최고크리에이티브책임자를 맡으며 첫 제품은 뷰티가 될 예정이다.",
  "CNBC", "https://www.cnbc.com/2026/09/21/revolve-partnership-donatella-versace.html"),
 ("컨덕터(Conductor SEO), 검색최적화 확대 속 CEO 교체",
  "SEO 플랫폼 컨덕터가 출시 1년 만에 고객이 12배 늘었다고 밝히며 최고제품책임자였던 웨이 정을 새 CEO로 선임했다. 답변엔진최적화(AEO)를 다음 성장축으로 삼는다.",
  "citybiz", "https://www.citybiz.co/article/908623/conductor-expands-aeo-investment-as-wei-zheng-takes-over-as-ceo/"),
 ("클리어채널 아웃도어, 옥외광고 옴니채널 측정 첫 통합",
  "클리어채널 아웃도어가 라이브램프의 크로스미디어 인텔리전스에 옥외광고사 최초로 연동됐다. 트랜스유니언과도 손잡아 OOH 노출 데이터를 CTV·검색·소셜과 같은 틀에서 측정할 수 있게 됐다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/clear-channel-outdoor-brings-out-of-home-into-omnichannel-measurement-with-radar-expansion-302888589.html"),
 ("2026년 상반기 최고의 브랜드 캠페인은 '솔직함'을 팔았다",
  "마케팅다이브가 선정한 상반기 최고의 캠페인들은 과장된 메시지 대신 솔직하고 투명한 화법을 택했다. 다만 소비자가 이를 실제 구매로 연결하는지는 지켜봐야 한다고 짚었다.",
  "Marketing Dive", "https://www.marketingdive.com/news/best-brand-campaigns-h1-2026-sold-honesty-consumers-buying/819960/"),
 ("가트너(Gartner) \"에이전틱 AI(Agentic AI) 프로젝트 40% 이상 2027년까지 폐기\"",
  "가트너는 비용 급증과 불명확한 사업 가치, 미흡한 리스크 관리로 에이전틱 AI 프로젝트의 40% 이상이 2027년 말까지 취소될 것이라고 전망했다. 실제 에이전트 기능을 갖춘 벤더는 극소수에 불과하다고 지적했다.",
  "Trullion", "https://trullion.com/blog/why-over-40-of-agentic-ai-projects-will-fail/"),
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
