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
 ("Baseten, 15억 달러 시리즈 F — AI 추론 인프라 최대 조달",
  "AI 추론 플랫폼 Baseten이 시리즈 F로 15억 달러를 조달하며 기업가치 130억 달러를 기록했다. 하루 10억 건 이상의 추론 요청을 처리하며 5개월 만에 기업가치가 160% 급등했다.",
  "Business Wire", "https://www.businesswire.com/news/home/20260622645563/en/Baseten-Raises-$1.5-Billion-to-Power-the-Next-Era-of-AI-Inference"),
 ("마이크로소프트, 자체 AI 모델 'MAI' 7종 공개",
  "마이크로소프트가 OpenAI 의존도를 줄이기 위해 자체 개발 AI 모델군 'MAI' 7종을 발표했다. 플래그십 추론 모델 MAI-Thinking-1은 저비용으로 프리미엄급 논리 결과물을 제공한다.",
  "CNBC", "https://www.cnbc.com/2026/06/02/microsoft-unveils-new-ai-models-lessen-reliance-on-openai-lower-costs.html"),
 ("배포 AI 에이전트 30개 중 9개만 벤치마크 공개 — MIT FAccT",
  "FAccT 2026(몬트리올)에서 발표된 MIT AI 에이전트 인덱스에 따르면, 실제 배포된 에이전트 30개 중 단 9개만 역량 벤치마크를 공개하고 대부분이 안전성 평가를 생략하고 있다.",
  "ACM Digital Library", "https://dl.acm.org/doi/10.1145/3805689.3806728"),
 ("Anthropic Claude Mythos, 오픈소스 취약점 2만 3천 건 탐지",
  "Anthropic의 Claude Mythos Preview가 1,000개 이상의 오픈소스 프로젝트를 분석해 23,019개의 취약점을 발견했다. 4월 출시 후 첫 달 활동 보고서에서 공개된 수치다.",
  "Anthropic", "https://www.anthropic.com/news"),
 ("6월 AI 모델 대전: Gemini 3.5 Pro · Claude Sonnet 4.8 동시 출격",
  "구글의 Gemini 3.5 Pro와 Anthropic의 Claude Sonnet 4.8이 6월 출시 경쟁에 돌입했다. 두 모델 모두 코딩·추론 성능 강화에 집중하며 엔터프라이즈 고객 확보를 겨냥한다.",
  "WaveSpeed Blog", "https://wavespeed.ai/blog/posts/june-2026-ai-launch-wave/"),
 ("엔비디아 DGX Spark에 오픈소스 에이전틱 환경 'NemoClaw' 탑재",
  "엔비디아가 DGX Spark 플랫폼 6월 업데이트에서 오픈소스 에이전틱 배포 환경 NemoClaw를 공개했다. 최적화된 로컬 모델·에이전트 오케스트레이션·보안 런타임을 하나의 패키지로 제공한다.",
  "LLM Stats", "https://llm-stats.com/llm-updates"),
 ("AI 자금, 추론 인프라 집중·아시아 주식 양극화",
  "6월 27일 기준 AI 투자금은 추론 컴퓨트 인프라로 급집중하며 단일 플랫폼이 15억 달러를 조달한 반면, 아시아 AI 관련 주식 시장은 서킷 브레이커가 발동될 만큼 급락했다.",
  "Asanify", "https://asanify.com/blog/news/inference-compute-funding-june-27-2026/"),
]

DESIGN = [
 ("캔 라이언즈 2026 디자인 그랑프리: Apple TV 리브랜드",
  "TBWA\\Media Arts Lab이 제작한 'Apple TV 리브랜드'가 캔 라이언즈 2026 디자인 그랑프리를 수상했다. 촉각적 유리 소재와 핀이아스 작곡 사운드 시그니처로 '인간이 만든 아름다움'을 구현했다.",
  "Branding in Asia", "https://www.brandinginasia.com/cannes-lions-2026-day-two-grand-prix-apac-winners-and-the-lionheart-award/"),
 ("아디다스 × 오아시스 'Original Forever', 같은 날 2관왕",
  "아디다스와 오아시스의 협업 'Original Forever'가 캔 라이언즈 2026 엔터테인먼트·스포츠 엔터테인먼트 두 부문에서 그랑프리를 동시에 차지했다. 브랜드와 팝 아이콘의 결합이 창의성의 새 기준을 제시했다.",
  "Variety", "https://variety.com/2026/biz/news/cannes-lions-entertainment-adidas-oasis-original-forever-1236791204/"),
 ("인터브랜드, 인하우스 팀으로 첫 셀프 리브랜드",
  "글로벌 브랜드 컨설팅사 인터브랜드가 자사 로고와 아이덴티티를 인하우스 팀이 직접 개편했다. 수십 년간 타사 브랜드를 만들어 온 에이전시가 자신의 정체성을 스스로 재정의한 이례적 사례다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_interbrand_done_in_house_2026.php"),
 ("'독서의 해 2026' 비주얼 아이덴티티, Fold7Design 제작",
  "Fold7Design이 영국 'National Year of Reading 2026' 공식 비주얼 아이덴티티를 공개했다. 친근한 타이포그래피와 생동감 있는 컬러 팔레트로 전 연령층을 아우르는 디자인을 선보였다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_national_year_of_reading_2026_by_fold7design.php"),
 ("여행 앱 'Going', DesignStudio와 새 이름·아이덴티티 완성",
  "저가 항공권 앱 '스캇 차프 플라이츠'가 'Going'으로 리네이밍하며 DesignStudio와 전면적인 브랜드 아이덴티티를 새롭게 구축했다. 출발의 설렘을 담은 다이내믹한 로고와 컬러 시스템이 핵심이다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_name_logo_and_identity_for_going_by_designstudio.php"),
 ("캔 라이언즈 Craft 부문: 수작업 크리에이티브가 AI를 앞서다",
  "캔 라이언즈 2026 Craft·Industry Craft·Film Craft 부문 최고상을 수작업 기반 크리에이티브가 독식했다. AI 자동화가 확산되는 가운데 인간 기술의 가치를 재조명하는 계기가 됐다.",
  "LBBOnline", "https://lbbonline.com/news/Cannes-Lions-2026-Grand-Prix-Winners-in-Entertainment-Gaming-Music-Sport-Design-Digital-Craft-Film-Craft-and-Industry-Craft"),
 ("Brand New(언더컨시더레이션), 웹사이트 전면 개편",
  "브랜드 아이덴티티 전문 미디어 Brand New가 웹사이트 UI와 로고를 새롭게 단장했다. 매일 업데이트되는 전 세계 리브랜드 사례를 더욱 직관적으로 탐색할 수 있게 됐다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_logo_and_website_for_brand_new_by_underconsideration.php"),
]

MARKETING = [
 ("워너브라더스 디스커버리, AWS 기반 에이전틱 AI 광고 기술 도입",
  "워너브라더스 디스커버리가 AWS와 협력해 전체 광고 기술 스택을 에이전틱 AI로 재구축한다. 미디어 플래닝·잠재고객 예측·어트리뷰션·주문 관리를 자동화해 선형 방송과 디지털 광고를 통합 운영한다.",
  "WBD Newsroom", "https://www.wbd.com/news/warner-bros-discovery-announces-agentic-ai-powered-advertising-technology-built-aws-its"),
 ("Yahoo DSP '에이전트 네트워크' 출시 — 23개 애드테크사 AI 통합",
  "야후 DSP가 캠페인 집행 AI를 외부 파트너에게 개방하는 '에이전트 네트워크'를 론칭했다. 광고주는 23개 파트너사의 AI 에이전트를 야후 DSP 내에서 조합해 타기팅·최적화·측정을 자동화할 수 있다.",
  "Yahoo Inc.", "https://www.yahooinc.com/press/yahoo-dsp-launches-agent-network-opening-the-ai-ecosystem-for-advertisers"),
 ("어도비, 캔 라이언즈서 GenStudio 에이전틱 AI 파트너십 발표",
  "어도비가 캔 라이언즈 2026에서 Anthropic·Microsoft·WPP·Omnicom과 GenStudio 기반 에이전틱 AI 파트너십을 체결했다. 자동차·제약·리테일 등 대형 광고주의 콘텐츠 제작부터 캠페인 최적화까지를 자동화한다.",
  "Business Wire", "https://www.businesswire.com/news/home/20260622975644/en/Adobe-Accelerates-Agentic-AI-Adoption-Through-New-Agency-and-Technology-Partnerships"),
 ("6월 27일 마테크·AI 소식: ActiveCampaign·Nudge 신제품 출시",
  "ActiveCampaign이 브랜드 가이드라인을 기억하는 Active Intelligence 2.8을 출시했고, 스타트업 Nudge는 AI 챗봇 내 제품 추천을 관리하는 Agentic Commerce Platform을 선보이며 프리시드 110만 달러를 조달했다.",
  "The Agile Brand Guide", "https://agilebrandguide.com/yesterdays-marketing-technology-ai-news-june-27-2026/"),
 ("마테크 시장 1만 5,505개 — AI 주도 '다윈 국면' 진입",
  "2026년 마테크 랜드스케이프가 1만 5,505개 제품으로 전년 대비 0.79% 성장에 그쳤다. 신규 1,488개 추가·1,367개 퇴출이 동시에 일어나며 AI 주도의 대규모 구조 재편이 진행 중이라는 분석이 나온다.",
  "CMSWire", "https://www.cmswire.com/digital-marketing/the-martech-landscape-has-plateaued-the-real-crisis-is-what-ai-is-exposing-underneath-it/"),
 ("2026년 브랜드 마케팅 12대 트렌드: AI와 인간 창의성의 공존",
  "Famous Campaigns가 2026 상반기를 분석한 결과, 에이전틱 AI 기반 캠페인 자동화와 진정성 있는 브랜드 스토리텔링의 공존이 핵심 트렌드로 부상했다. AI 획일화에 대한 반발로 차별화된 크리에이티브 수요가 커지고 있다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/06/the-12-trends-that-defined-brand-marketing-in-2026-so-far/"),
 ("AI 마케팅 에이전트 도입 기업 74%, 거버넌스 실패로 롤백",
  "AI 고객 응대 에이전트를 도입한 기업의 74%가 거버넌스 실패로 인한 고객 경험 훼손·신뢰도 하락 이후 에이전트를 철수한 것으로 나타났다. AI 마케팅 자동화만큼 안전장치 마련이 시급하다는 경고다.",
  "MarTech", "https://martech.org/martech-2026-ai-drives-a-major-industry-reset/"),
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
