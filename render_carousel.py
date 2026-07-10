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
 ("OpenAI, GPT-5.6 시리즈(Sol·Terra·Luna) 정식 공개",
  "OpenAI가 7월 9일 차세대 모델 GPT-5.6 Sol·Terra·Luna를 챗GPT와 API 전반에 공개했다. Sol은 소프트웨어 엔지니어링과 에이전틱 작업에서 역대 최고 성능을 보이며, 가격은 100만 토큰당 입력 5달러·출력 30달러부터 시작한다.",
  "OpenAI", "https://openai.com/index/previewing-gpt-5-6-sol/"),
 ("xAI, 'Opus급' 성능 내세운 Grok 4.5 출시",
  "xAI가 7월 8일 신모델 Grok 4.5를 공개했다. 일론 머스크는 이를 'Opus급이지만 더 빠르고 저렴한 모델'이라 소개했으며, Cursor와 공동 학습해 장시간 자율 코딩 작업에 강점을 갖췄다.",
  "TechCrunch", "https://techcrunch.com/2026/07/08/spacexai-releases-grok-4-5-which-elon-describes-as-an-opus-class-model/"),
 ("Anthropic, 2차 시장 밸류에이션서 OpenAI 추월",
  "Anthropic의 비상장 주식 2차 시장 가치가 1조 2000억 달러까지 치솟아 OpenAI(약 9080억 달러)를 넘어섰다. 코딩 에이전트 Claude Code의 매출 성장이 주요 동력으로 꼽힌다.",
  "TipRanks", "https://www.tipranks.com/news/anthropics-secondary-valuation-rockets-to-1-2-trillion-topping-openai"),
 ("UN, AI 거버넌스 국제 공조 촉구 — \"파국적 위해\" 경고",
  "제네바에서 열린 UN AI 글로벌 대화에서 요슈아 벤지오 등 전문가들이 AI가 여러 영역에서 인간 능력을 넘어서고 있으며, 과학적 이해와 정부 대응 속도를 앞지르고 있다고 경고했다.",
  "UN News", "https://news.un.org/en/story/2026/07/1167862"),
 ("중국 Z.ai GLM-5.2, 저비용으로 미국 AI 추격",
  "중국 Z.ai가 공개한 GLM-5.2가 Opus 4.8과 성능 격차를 1%로 좁히며 오픈소스 코딩 벤치마크 1위에 올랐다. 비용은 경쟁 모델의 약 6분의 1 수준으로, 미·중 AI 격차 논쟁에 불을 지폈다.",
  "Euronews", "https://www.euronews.com/next/2026/07/03/what-is-glm-52-the-new-chinese-ai-model-thats-rivalling-anthropic"),
 ("차마스 팔리하피티야, AI 코딩 스타트업 8090 랩스 CEO로",
  "차마스 팔리하피티야가 세일즈포스 벤처스 주도로 1억 3500만 달러 시리즈A를 유치하고 8090 랩스 CEO를 맡았다. 규제 산업을 겨냥한 엔터프라이즈 AI 코딩 플랫폼 '소프트웨어 팩토리'를 키운다.",
  "TechCrunch", "https://techcrunch.com/2026/06/29/chamath-palihapitiya-raises-135m-series-a-for-his-ai-coding-startup-takes-ceo-role/"),
 ("Reflection AI, 스페이스X 콜로서스와 63억 달러 컴퓨팅 계약",
  "오픈소스 AI 스타트업 Reflection AI가 스페이스X의 멤피스 데이터센터에서 엔비디아 GB300 칩에 접근하는 63억 달러 규모 컴퓨팅 임대 계약을 체결했다. 2029년까지 매달 1억 5000만 달러를 지불한다.",
  "CNBC", "https://www.cnbc.com/2026/06/22/spacex-ai-colossus-data-center-reflection.html"),
]

DESIGN = [
 ("스포르팅 CP, 25년 만의 리브랜드 — 1940년대 엠블럼으로 회귀",
  "포르투갈 축구클럽 스포르팅 CP가 창단 120주년을 맞아 JKR과 함께 25년 만에 첫 리브랜드를 단행했다. 1945년 사자 엠블럼을 재해석하고 전용 서체 '스포르팅 산스'를 새로 만들었다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_sporting_clube_de_portugal_by_jones_knowles_ritchie.php"),
 ("유튜브, 창립 20주년 맞아 브랜드 아이덴티티 전면 개편",
  "유튜브가 창립 20주년을 맞아 전용 서체와 첫 모션 아이덴티티, 레드-마젠타 그라데이션을 도입한 글로벌 비주얼 리프레시를 공개했다. 유튜브TV·쇼츠·뮤직 등 하위 브랜드 전반의 통일감을 높였다.",
  "Marketing Dive", "https://www.marketingdive.com/news/youtube-revamps-visual-identity-as-entertainment-landscape-shifts/809983/"),
 ("피그마 Config 2026, 캔버스에 모션·3D·코드 레이어 도입",
  "피그마가 연례 컨퍼런스 Config 2026에서 캔버스 위 모션 편집, 3D 변형·셰이더, 코드 레이어 등을 대거 공개했다. AI 에이전트가 코드를 직접 생성·수정하는 기능도 비공개 베타로 시작된다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("타이포 스튜디오 NaN, 독립 폰트 마켓 'fonts.xyz' 오픈",
  "디저·와이즈 등의 전용 서체를 만든 파운드리 NaN이 독립 폰트 재단들을 위한 마켓플레이스 fonts.xyz를 열었다. 로열티 80%, 통합 라이선스를 내세워 기존 플랫폼 독점 구조에 도전한다.",
  "Typography.Guru", "https://typography.guru/weekly/arc2/no143/new-font-platform-fontsxyz-has-launched-r1260"),
 ("히어로 모토코프의 VIDA, 새 아이덴티티로 아시아 기록 등재",
  "인도 전기이륜차 브랜드 VIDA(히어로 모토코프)가 모기업 로고에서 딴 기하학적 'V' 레터마크와 모션·사운드 시그니처 '더 붐'을 갖춘 새 아이덴티티를 공개했다. 최대 브랜드 로고로 아시아 기록에도 등재됐다.",
  "mediabrief", "https://mediabrief.com/vida-powered-by-hero-unveils-new-brand-identity/"),
 ("크리에이티브 붐 설문 — 디자이너 69% \"최근 1년 번아웃 경험\"",
  "크리에이티브 붐이 전 세계 창작자 882명을 조사한 결과 69%가 최근 12개월 내 번아웃을 겪었다고 답했다. AI 도입 확산 속 보수·업무량에 대한 업계의 피로감이 드러났다.",
  "Creative Boom", "https://www.creativeboom.com/news/the-state-of-the-creative-industry-2026-what-our-survey-tells-us-about-money-burnout-and-ai/"),
 ("싱가포르 국제 주얼리 엑스포, 2026 에디션 앞두고 리브랜드",
  "SIJE가 7월 9~12일 개최를 앞두고 새 로고와 다이아몬드 아이콘, 딥퍼플 컬러 팔레트를 적용한 리프레시된 아이덴티티를 공개했다.",
  "Diamond World", "https://www.diamondworld.net/news/sije-unveils-new-brand-identity-for-2026-edition"),
]

MARKETING = [
 ("메타 'Muse Image', 사생활 논란 속 페이스북·인스타·왓츠앱 확대",
  "메타가 AI 이미지 생성기 'Muse Image'를 인스타그램·왓츠앱 등으로 확대했다. 타인의 공개 프로필을 태그해 초상을 활용한 이미지를 만들 수 있어 동의 없는 사용에 대한 우려가 커지고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/07/07/meta-rolls-out-muse-a-new-ai-image-generator/"),
 ("펩시, '동의' 연상 문구 논란에 와일드 체리 게시물 삭제·사과",
  "펩시가 \"일반 체리가 허락을 구하지 않게 된 것\"이라는 와일드 체리 홍보 문구가 동의 개념을 가볍게 다뤘다는 비판을 받자 게시물을 삭제하고 공개 사과했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/pepsi-apologises-after-wild-cherry-post-sparks-backlash-over-consent-reference-12147426"),
 ("구글, 인도 시장에 제미나이 기반 '에이전틱 마케팅' 도구 공개",
  "구글이 인도향으로 리드용 비즈니스 에이전트, 유튜브 브랜드스택, AI 맥스 포 쇼핑 등 제미나이 기반 광고 도구를 선보였다. 검색광고 안에 챗봇 에이전트를 넣어 잠재고객과 대화하는 기능이 특징이다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/google-brings-gemini-powered-ad-tools-to-india-with-focus-on-agentic-marketing-12149166"),
 ("챗GPT 광고, Criteo 통해 집행 브랜드 2000곳 돌파",
  "오픈AI의 챗GPT 광고 파일럿에 Criteo를 통해 참여한 브랜드가 2000곳을 넘어섰다. 신규 크리에이티브 포맷 'Prompt Smart Ads'는 광고 지출을 최대 4배까지 끌어올린 것으로 나타났다.",
  "PPC Land", "https://ppc.land/criteo-hits-2-000-brands-on-chatgpt-ads-as-prompt-smart-ads-show-4x-spend-lift/"),
 ("인스타그램, 릴스 '포스트뷰' 광고 전 세계 광고주로 확대",
  "인스타그램이 60초 이상 릴스 시청 종료 직후 노출되는 '포스트뷰' 광고 포맷을 전 세계 모든 광고주로 확대했다. 기존 제한된 그룹에만 열려 있던 지면을 넓힌 것이다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/instagram-expands-reels-post-view-ads-to-all-advertisers/822317/"),
 ("영국 베리 생산자 단체, 학교 주변 정크푸드 옥외광고 규제 촉구",
  "영국 베리 생산자 단체가 패스트푸드 광고판을 신선 베리로 바꾸는 패러디 캠페인으로 학교 반경 400m 내 정크푸드 옥외광고 규제를 요구했다. 아동 91%가 등하굣길에 정크푸드 광고에 노출된다는 조사 결과도 함께 공개했다.",
  "Retail Times", "https://retailtimes.co.uk/british-berry-growers-launches-campaign-against-fast-food-ooh-advertising/"),
 ("WHOOP, 나이키 출신 전 CMO를 최고마케팅책임자로 영입",
  "웨어러블 브랜드 WHOOP가 회원 300만 명 돌파를 계기로 나이키 출신의 전 글로벌 CMO 디르크얀 반 하메런을 신임 CMO로 영입했다. 그는 나이키에서 '드림 크레이지' 캠페인과 올림픽 마케팅을 이끈 인물이다.",
  "Adweek", "https://www.adweek.com/brand-marketing/whoop-hires-former-nike-cmo-as-its-top-marketer/"),
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
