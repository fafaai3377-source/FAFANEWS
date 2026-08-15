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
 ("OpenAI, 'GPT-5.6 Sol' 14배 빠른 초고속 모드 출시",
  "OpenAI가 최신 모델 GPT-5.6 Sol을 위한 'Ultrafast' 모드를 공개했다. 칩 스타트업 Cerebras와의 파트너십을 바탕으로 초당 최대 750토큰, 표준 대비 14배 빠른 속도를 낸다. 현재 일부 기업 고객에게 프리뷰로 제공되며 고객 서비스·금융 분석 등에 활용될 전망이다.",
  "TechCrunch", "https://techcrunch.com/2026/08/13/openai-introduces-ultrafast-a-new-mode-that-makes-gpt-5-6-sol-work-at-14x-the-speed/"),
 ("구글, AI 생성물 워터마크 제거 기능 허용",
  "구글이 제미나이·플로우 등에서 만든 이미지·영상·음악의 화면 표시 워터마크를 사용자가 끌 수 있도록 했다. 다만 추적을 위한 비가시 SynthID 워터마크와 C2PA 메타데이터는 그대로 유지된다. 구글은 AI 생성물 출처 검증용 오픈소스 라이브러리 'Credentio'도 함께 공개했다.",
  "TechCrunch", "https://techcrunch.com/2026/08/14/google-will-now-allow-users-to-remove-visible-watermark-from-its-ai-generations/"),
 ("OpenAI 투자사 스라이브 홀딩스, 12조원 밸류에이션에 2조원 조달",
  "조시 쿠슈너가 이끄는 스라이브 홀딩스가 소프트뱅크·D1 캐피탈·알티미터 등으로부터 20억 달러(약 2조원)를 유치해 기업가치 120억 달러를 인정받았다. 전통 기업을 인수해 AI를 접목하는 전략으로, 회계·IT 부문 70여 개 기업을 운영 중이며 OpenAI가 지분을 보유하고 인력까지 파견하고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/openai-backed-thrive-holdings-raises-2b-to-bring-ai-to-the-enterprise/"),
 ("앤스로픽, 클로드 코드 '오토 모드' 기본값으로 전환",
  "앤스로픽이 8월 14일부터 프로·맥스·팀 요금제에서 클로드 코드의 '오토 모드'를 기본으로 켠다. 되돌릴 수 없거나 파괴적인 작업이 아니면 사람 승인 없이 진행되며, 안전성 테스트에서 오토 모드가 유해 행동의 89%를 걸러내 사람 검토(13.6%)보다 훨씬 효과적이었다고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/08/09/anthropic-is-turning-claude-codes-auto-mode-on-by-default/"),
 ("애플, 시리 뉴스 기능 위해 언론사와 콘텐츠 계약 협상",
  "애플이 실시간 뉴스를 제공하는 AI 시리를 위해 언론사들과 콘텐츠 사용 협상을 진행 중이다. 고정 라이선스료 대신 콘텐츠 사용량에 따라 지급하는 방식을 제안했으며, 규모는 억대 달러(9자리)에 이를 수 있다. 업그레이드된 시리는 올해 하반기 출시가 예정돼 있다.",
  "TechCrunch", "https://techcrunch.com/2026/08/13/apple-in-talks-to-pay-publishers-to-provide-siri-with-current-news-report/"),
 ("우버·포니에이아이, 유럽에 로보택시 2천 대 투입 계획",
  "우버와 중국 자율주행 업체 포니에이아이(Pony.ai)가 유럽 4개 도시에 로보택시 2,000대를 배치하는 파트너십 확장을 발표했다. 포니에이아이가 자율주행 기술을, 우버가 호출 플랫폼을 제공하는 구조로 구체적 도시명과 일정은 단계적으로 공개할 예정이다.",
  "TechCrunch", "https://techcrunch.com/2026/08/14/uber-and-pony-ai-plan-to-bring-2000-robotaxis-to-europe/"),
 ("구글 제미나이 앱, 월간 사용자 10억 명 돌파",
  "순다르 피차이 구글 CEO가 제미나이 앱이 월간활성사용자(MAU) 10억 명을 넘었다고 발표했다. 검색·지메일 등에 이어 구글의 14번째 '10억 사용자' 제품이자 역대 가장 빠르게 성장한 제품으로, 사용자의 63%가 음성 기능을 쓰고 하루 1억5천만 장 이상의 이미지가 생성된다.",
  "TechCrunch", "https://techcrunch.com/2026/08/11/googles-gemini-app-surges-to-one-billion-users/"),
]

DESIGN = [
 ("인스타그램, 10년 만에 브랜드 리프레시 단행",
  "인스타그램이 2016년 이후 처음으로 커서브 워드마크를 새로 다듬은 브랜드 리프레시를 공개했다. 손글씨 느낌은 유지하되 획을 더 정돈하고 새 서체 세 종을 도입했으며, 수백 개 시안을 검토한 끝에 원래의 필기체 감성으로 돌아왔다. 아담 모세리는 새 워드마크를 '더 선명하고 현대적'이라 설명했고, 나머지 브랜드 시스템은 2026년 내내 순차 적용될 예정이다.",
  "Creative Boom", "https://www.creativeboom.com/news/instagram-reveals-its-first-brand-refresh-in-10-years-with-a-new-wordmark-and-three-typefaces/"),
 ("세븐업, 15년 만의 최대 브랜드 개편 '라임 레몬'",
  "커리그 닥터페퍼가 세븐업을 15년 만에 가장 큰 폭으로 리뉴얼하며 '레몬 앤 라임' 대신 '라임 레몬'으로 이름을 바꿨다. 레시피의 라임 비중을 높인 데 맞춰 세로형 새 로고와 더 대담한 컬러, 새 패키지 디자인을 전면 교체했다. 리저널·체리 세븐업 등 전 라인업에 새 아이덴티티가 8월 중순부터 순차 적용된다.",
  "Keurig Dr Pepper", "https://www.keurigdrpepper.com/7up-puts-lime-in-the-spotlight-with-its-biggest-brand-evolution-in-more-than-15-years/"),
 ("\"애플워치 재설계, 더는 늦출 수 없다\"",
  "애플이 신임 하드웨어 총괄 존 터너스 체제 아래 애플워치의 대대적인 디자인 개편을 검토 중이라는 보도가 나왔다. 애플 전문 리커 마크 거먼에 따르면 산업디자인팀이 지난 1년여간 여러 방향의 스마트워치 재설계를 탐색해왔다. Creative Bloq는 최근 수년간 정체됐던 애플 디자인 언어에 변화가 필요한 시점이라고 짚었다.",
  "Creative Bloq", "https://www.creativebloq.com/design/product-design/the-apple-watch-redesign-cant-come-soon-enough"),
 ("피그마, 오래 기다린 '중첩 폴더' 기능 출시",
  "피그마가 파일 관리 체계를 밑바닥부터 다시 설계해 최대 10단계까지 중첩 가능한 폴더 기능을 선보였다. 폴더별 색상 지정과 상위 폴더 권한 상속 등 팀 규모가 커진 조직의 파일 정리를 돕는 기능으로, 전 플랜 사용자에게 수 주에 걸쳐 순차 배포된다. 피그마 블로그는 엔지니어·디자이너·PM이 역할 구분 없이 협업한 개발 과정도 함께 소개했다.",
  "Figma Blog", "https://www.figma.com/blog/code-craft-and-the-making-of-nested-folders/"),
 ("TBWA, 새 글로벌 비주얼 아이덴티티 공개",
  "TBWA의 디자인 조직 DXD가 네트워크 전체를 관통하는 새 글로벌 비주얼 아이덴티티를 공개했다. 손글씨 브러시와 잉크 드롭 같은 수공예적 요소, 세리프 서체 재도입, 새 보조 색상 '아날로그'를 더하면서도 시스템의 약 20%는 지역별 커스터마이징 여지로 남겨뒀다. '디스럽션 컴퍼니'라는 브랜드 철학을 유지하면서도 각 지사의 문화적 색깔을 담으려는 시도다.",
  "adobo Magazine", "https://www.adobomagazine.com/design/dxd-brings-design-by-disruption-to-life-through-tbwas-new-global-identity/"),
 ("\"업계 최악은 지났다\" — 8월 크리에이티브 업계 동향",
  "크리에이티브 붐의 월간 인사·스튜디오 소식 코너 '붐스 & 셰이크스'가 8월호를 공개했다. M+C 사치의 승진·영입 소식 등 업계 전반의 채용과 신규 런칭을 다뤘다. 지난 18개월간 자금난에 시달렸던 업계 리더들 사이에서 '최악은 지났다'는 낙관적 분위기가 감지된다고 전했다.",
  "Creative Boom", "https://www.creativeboom.com/news/booms-shakes-augusts-biggest-hires-launches-and-wins-from-across-the-industry/"),
 ("어도비, 챗GPT 안에 크리에이티브 툴 70여 개 통합",
  "어도비가 포토샵, 익스프레스, 프리미어, 일러스트레이터 등 70여 개 크리에이티브 기능을 하나로 묶은 통합 플러그인을 챗GPT에 출시했다. 대화형 AI 워크플로우 안에서 디자인 작업을 바로 실행할 수 있게 됐다. 디자인 툴과 생성형 AI 챗봇의 경계가 한층 더 흐려지고 있다는 평가다.",
  "Design Week", "https://www.designweek.co.uk/adobe-launches-unified-chatgpt-plugin-to-streamline-ai-powered-design-workflows/"),
]

MARKETING = [
 ("구글 픽셀, '리클레임 더 모먼트' 글로벌 캠페인 출시",
  "구글이 픽셀 11 시리즈의 HiLight LED 기술을 앞세운 글로벌 캠페인 'Reclaim The Moment'를 공개했다. NBA 스타 스테픈 커리가 출연한 'Uninterrupted' 광고를 통해 디지털 방해에서 벗어나 순간에 집중하자는 메시지를 전한다.",
  "MediaPost", "https://www.mediapost.com/publications/article/417195/"),
 ("도브, US오픈 공식 후원사로 복귀 '스포츠 x 스타일' 캠페인",
  "도브가 US오픈 공식 언더암 스폰서로 복귀하며 'Don't Sweat It. Make A Racket.' 캠페인을 선보였다. 토너먼트 패션을 담는 포토그래퍼 협업, 팬 인터뷰 기반 소셜 시리즈 'Racquet Report', 여성 오너 바 10곳 후원 등을 결합해 자신감과 여성 임파워먼트 메시지를 자사의 인비저블 제품과 함께 전달한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/doves-marketing-mixes-sports-and-style-for-us-open-sponsorship-return/827652/"),
 ("늘어나는 마테크 스택, 오히려 생산성 갉아먹는다",
  "Pipedrive의 2026 CRM 트렌드 리포트에 따르면 영업·마케팅 담당자 1000명 중 약 4분의 3이 고객 정보를 온전히 파악하려면 최소 2개 이상의 시스템이 필요하다고 답했다. 62%는 도구 간 연결 부족으로 매주 한 번 이상 중요한 액션이나 기회를 놓친다고 응답해, 마테크 스택 과잉이 오히려 업무 효율을 떨어뜨리고 있음을 보여준다.",
  "MarTech", "https://martech.org/is-your-martech-stack-costing-more-time-than-it-saves/"),
 ("버슨, AI 예측 인텔리전스 기업 림빅 인수",
  "글로벌 커뮤니케이션 에이전시 버슨이 AI 기업 림빅(Limbik)을 인수하고 자사의 예측 인텔리전스 플랫폼 '디사이퍼(Decipher)'를 내재화했다. 디사이퍼는 60개 이상 시장에서 메시지의 확산성과 신뢰도를 분석해 커뮤니케이션 반응을 예측하는 도구로, 림빅 공동창업자들은 버슨의 글로벌 이노베이션 리더로 합류한다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/burson-acquires-ai-company-limbik-brings-decipher-platform-in-house-12260001"),
 ("아디티아 비를라 그룹, 계열사에 브랜드 로열티 도입",
  "아디티아 비를라 그룹이 그라심, 힌달코, 노벨리스 등 상장 계열사를 대상으로 매출의 0.25%를 브랜드 로열티로 징수하는 정책을 6월부터 시행했다. 기업당 연간 최대 225억 루피 한도가 적용되며, 경영진은 이를 가족 경영 체제에서 벗어난 '구조화된 거버넌스'로의 전환이라고 설명했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/aditya-birla-group-introduces-brand-royalty-for-grasim-hindalco-and-novelis-12259664"),
 ("타이어 브랜드 BKT, 소비자 마케팅으로 인지도 11% 상승",
  "오프하이웨이 타이어로 유명한 BKT가 인도 소비자용 타이어 시장 공략을 위한 마케팅으로 브랜드 인지도를 11% 끌어올렸다. CMO 마헤시 코파드는 리니어 TV와 스포츠 후원으로 도달을 넓히는 동시에 디지털·딜러 채널과 구매 후 케어 프로그램 'YOU FORWARD'로 실질 판매 전환을 노리겠다고 밝혔다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/bkts-consumer-push-delivers-11-awareness-growth-now-it-wants-visibility-to-drive-sales-12258877"),
 ("마마어스 모회사 호나사, 광고비 16.7% 늘리며 매출 성장 견인",
  "인도 뷰티 기업 호나사 컨슈머(마마어스 모회사)가 2027회계연도 1분기 광고비를 전년 대비 16.7% 늘린 241크로 루피로 집행했다. 같은 기간 매출은 27% 성장한 755.9크로 루피를 기록했고, 자회사 더 더마 코는 연환산 순매출 1000크로 루피를 돌파하며 마케팅 투자 확대가 성장으로 이어지고 있음을 보여줬다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/honasa-consumer-raises-ad-spend-167-to-rs-241-crore-in-q1-fy27-as-revenue-growth-outpaces-marketing-investment-12259044"),
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
