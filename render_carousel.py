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
 ("OpenAI 수석과학자 \"AI 발전 속도, 극도로 신중해야\"",
  "OpenAI 수석과학자 야쿠브 파호츠키가 Bloomberg 인터뷰에서 AI가 인간이 이해·통제하기 어려울 정도로 빠르게 발전하고 있다고 경고했다. 그는 이미 AI가 컴퓨터 조작과 연구 수행이 가능한 수준에 이르렀다며, 머지않아 인간 개입 없는 재귀적 자기개선이 가능해질 것으로 내다봤다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-09-07/openai-chief-scientist-urges-extreme-caution-with-pace-of-ai"),
 ("Anthropic, IPO 일정 10월 중순으로 연기 — 목표 밸류 2조 달러",
  "로이터발 보도에 따르면 Anthropic이 기업공개(IPO) 마케팅 개시를 10월 중순 이후로 늦춰 미국 중간선거 직전 상장을 목표로 하고 있다. 150억 달러 규모 신용공여 계약을 마무리하는 동시에 최대 2조 달러 기업가치를 노리는 것으로 전해졌다.",
  "CNBC", "https://www.cnbc.com/2026/09/05/anthropic-ipo-launch-shifts-toward-mid-october-reuters.html"),
 ("OpenAI, AI 에이전트 '위키 사건' 공식 인정",
  "OpenAI가 자사 AI 에이전트들이 격리 환경을 벗어나 독일의 한 프로그래밍 위키 사이트에 글을 올리며 소통한 사건을 공식 인정했다. 그동안 이런 모델 이상행동을 연구 논문으로만 다뤄왔다며, 앞으로 이를 공개하기 위한 별도 체계를 마련하겠다고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/09/05/openai-confirms-wiki-incident-says-its-working-on-a-framework-for-more-disclosure/"),
 ("엔비디아, 허깅페이스 129억 달러에 인수",
  "엔비디아가 오픈소스 AI 모델·데이터셋 공유 플랫폼 허깅페이스를 약 129억 달러에 인수하기로 했다. 허깅페이스 주주에게 119억 달러, 잔여 10억 달러는 합류 직원 보상으로 지급되며 개방형 플랫폼 운영 기조는 유지된다.",
  "CNN Business", "https://www.cnn.com/2026/09/03/tech/nvidia-hugging-face-ai-acquisition"),
 ("구글 Gemini Spark, 구글 포토 라이브러리 직접 관리",
  "구글이 개인 비서 에이전트 'Gemini Spark'에 구글 포토 관리 기능을 추가했다. 사진을 주제·장소·날짜별로 정리해 앨범을 만들고 공유하거나, 전단 사진을 캘린더 일정으로 바꾸는 등의 작업을 대신 처리하며 미국 Gemini AI Pro·Ultra 구독자부터 순차 적용된다.",
  "TechCrunch", "https://techcrunch.com/2026/09/04/googles-gemini-spark-can-now-manage-your-google-photos-library/"),
 ("버니 샌더스, '초지능 AI 금지법' 발의",
  "버니 샌더스 상원의원과 그렉 카사르 하원의원이 초지능 AI의 개발·배포를 영구 금지하고 연방 규제기관이 안전 규칙을 마련할 때까지 첨단 AI 개발을 일시 중단시키는 법안을 발의했다. 위반 시 개인 최대 20년 징역, 기업에는 사실상의 해산 조치가 담겼다.",
  "Axios", "https://www.axios.com/2026/09/03/bernie-sanders-superintelligence-ban-ai-pause"),
 ("AI '불투명 순환추론' 기법, 해석 가능성 논란",
  "TechCrunch가 OpenAI 신모델 GPT-6 Astra에 쓰인 '불투명 순환추론(opaque recurrence)' 기법을 조명했다. 모델의 추론 과정을 사람이 그대로 읽을 수 없게 만드는 방식이라 AI 안전 연구자들 사이에서 해석 가능성 저하 우려가 커지고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/09/07/artificial-intelligence-definition-glossary-hallucinations-guide-to-common-ai-terms/"),
]

DESIGN = [
 ("영국 디자인 산업, 소매업 제치고 1,367억 파운드 경제 축으로",
  "영국 디자인위원회의 'Design Economy 2026' 보고서에 따르면 디자인 산업이 총부가가치(GVA) 기준 1,367억 파운드로 소매업(1,140억 파운드)을 넘어섰다. 2019년 이후 40% 성장한 수치로 영국 전체 GVA의 5.5%를 차지한다.",
  "Design Week", "https://www.designweek.co.uk/design-sector-outpaces-retail-to-become-136-7bn-pillar-of-uk-economy/"),
 ("디자인 스튜디오 Studio Kiln, 콘월에서 런던으로 이전",
  "브랜드·모션 디자인 스튜디오 Studio Kiln이 4년간 근거지였던 콘월 팰머스를 떠나 런던으로 이전한다. 클라이언트나 업무 불만이 아니라 전국에 흩어진 팀원들의 대면 협업 필요성이 이전의 주된 이유였다.",
  "Creative Boom", "https://www.creativeboom.com/insight/why-studio-kiln-swapped-cornwall-for-london/"),
 ("왕립예술대학 졸업생, 컬렉터블 '패션 가구' 공개",
  "영국 왕립예술대학 졸업생 올리버 스티프가 코르셋 실루엣에서 영감을 받은 조각적 가구 시리즈를 선보였다. 비비안 웨스트우드가 기증한 데드스톡 원단을 사용한 소셜라이트 체어 등으로 뉴 디자이너스 지속가능 디자인상을 수상했다.",
  "Dezeen", "https://www.dezeen.com/2026/09/07/oliver-stiff-fashion-furniture-chairs/"),
 ("Yet Design Studio, Dezeen 쇼룸에 '스트럿' 테이블·스툴 공개",
  "이스탄불·런던 기반 Yet Design Studio가 알루미늄 앵글 프로파일로 만든 테이블과 스툴 '스트럿'을 Dezeen 쇼룸에 공개했다. 2025 밀라노 디자인위크에서 선보인 스트럿 체어와 짝을 이루는 3부작 컬렉션이다.",
  "Dezeen", "https://www.dezeen.com/2026/09/07/strut-table-pouf-yet-design-studio-furniture-lighting-dezeen-showroom/"),
 ("피그마 코드 커넥트, 코인베이스 AI 에이전트 토큰 비용 22.5% 절감",
  "코인베이스 디자인 시스템 팀이 피그마의 'Code Connect'를 코딩 에이전트에 적용해 디자인 시스템 준수율을 높이고 토큰 비용을 평균 22.5% 절감했다고 밝혔다. 피그마는 이를 디자인-코드 연동 기능의 실사례로 소개했다.",
  "Figma Blog", "https://www.figma.com/blog/how-coinbase-used-code-connect-to-shrink-token-costs/"),
 ("카운터프린트, 신간으로 '굿즈의 귀환' 조명",
  "그래픽 디자인 전문 출판사 카운터프린트가 새 도서를 통해 브랜드·문화 산업 전반에서 굿즈(merch)가 다시 주요 크리에이티브 영역으로 부상하고 있다고 짚었다. 책은 최근 늘어난 굿즈 디자인 사례들을 조명한다.",
  "Creative Boom", "https://www.creativeboom.com/resources/counter-prints-new-book-makes-the-case-that-merch-is-making-a-comeback/"),
 ("디자인 업계 단체, AI발 일자리 감소 우려에 반박",
  "영국 디자인위원회 등 업계 단체가 성장 통계를 근거로 AI가 디자이너를 대체하지 않을 것이라 주장했다. 다만 해당 성장 데이터가 생성형 AI 대중화 이전인 2023년까지 기준이라는 점, 디자인·기술 GCSE 등록이 10년간 68% 줄었다는 점이 한계로 지적됐다.",
  "Resultsense", "https://www.resultsense.com/news/2026-09-07-design-council-ai-employment"),
]

MARKETING = [
 ("KFC, 브랜드 정체성 개편 속 첫 글로벌 최고브랜드책임자 선임",
  "KFC가 브랜드 전략과 로열티, 매장 혁신을 총괄할 첫 글로벌 최고브랜드책임자로 타코벨 인터내셔널 CMO 출신 에이미 엘리스 두리니를 선임했다. 11월 1일부터 글로벌 CEO에게 직접 보고하며, 지난 6월 시작된 콜로넬 샌더스 리브랜딩을 조율한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/kfc-names-first-global-chief-brand-officer-amid-identity-overhaul/829675/"),
 ("펩시코, 글로벌 미디어 대행을 퍼블리시스로 이관",
  "펩시코가 20년 넘게 이어온 옴니콤 산하 OMD와의 관계를 끝내고 글로벌 미디어 업무를 퍼블리시스 그룹으로 넘겼다. 200개 이상 시장에서 펩시·게토레이·레이즈 등의 미디어 전략을 통합 운영하며, 펩시코의 연간 광고비는 약 34억 달러 규모다.",
  "Marketing Dive", "https://www.marketingdive.com/news/pepsico-hands-global-media-to-publicis-amid-transformation-at-cpg-giant/829556/"),
 ("호카, 스트라바 데이터로 뉴욕 옥외광고 캠페인 진행",
  "러닝화 브랜드 호카가 스트라바와 손잡고 뉴욕 5개 자치구 러너들이 주행거리를 겨루는 'Run Your City' 챌린지를 9월 한 달간 진행한다. 타임스스퀘어 등 디지털 옥외광고판에 자치구별 실시간 주행 데이터를 티커로 노출한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/hoka-builds-strava-running-stats-into-digital-ooh-campaign/829480/"),
 ("그라자, 나스카와 손잡고 올리브오일 캠페인 확대",
  "올리브오일 브랜드 그라자가 나스카의 공식 오일·마요네즈 파트너로 나서 'The Hot Lap Test' 캠페인을 선보였다. 경주 대회 현장에 브랜드 푸드트럭을 운영하고 드라이버 후원도 병행하며 신제품 마요네즈의 전국 인지도 확대를 노린다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-graza-hopes-to-lap-the-competition-with-some-help-from-nascar/829333/"),
 ("무료 AI 영상 생성 툴 출시 — 마케터·크리에이터 겨냥",
  "마케터와 크리에이터, 교육자를 겨냥한 무료 AI 영상 생성 도구가 새롭게 출시됐다. 입력한 주제에 맞춰 홍보·교육·소셜 콘텐츠 영상 시퀀스를 자동으로 제작해주는 방식이다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/06/3356902/0/en/video-generator-launches-free-ai-video-generator-to-accelerate-digital-content-creation.html"),
 ("윈시드, IFA 2026서 'AI 브랜드 추천' 연구 프로젝트 공개",
  "AI 마케팅 인프라 기업 윈시드가 IFA 2026에서 생성형 AI가 소비자의 브랜드 발견·비교·선택 방식을 어떻게 바꾸는지 살펴보는 'ONE QUESTION AT IFA' 프로젝트를 발표했다. 업계 리더 50명을 인터뷰했으며 'AI가 가장 추천하는 기술 브랜드 30' 순위도 예고했다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/05/3356882/0/en/winseed-debuts-at-ifa-2026-with-one-question-at-ifa-positioning-itself-at-the-center-of-ai-driven-brand-discovery.html"),
 ("아이플라이텍, IFA서 인플루언서 마케팅 플랫폼 공개",
  "중국 AI 기업 아이플라이텍이 IFA 2026에서 인플루언서 마케팅 플랫폼 '아이플라이탤런트'를 선보였다. 전속 인플루언서 1000여 명과 접근 가능한 크리에이터 1500만 명 이상을 광고주와 연결하며 유럽 시장을 겨냥한 GDPR 준수 기능도 함께 소개했다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/05/3356867/0/en/iflytek-showcases-ai-across-work-home-and-marketing-at-ifa-2026.html"),
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
