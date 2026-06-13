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
 ("Anthropic, 기업가치 9650억 달러에 IPO 비밀 파일링",
  "클로드를 개발한 Anthropic이 SEC에 IPO 비밀 파일링을 제출했다. 5월 연간 매출 470억 달러 달성을 발표한 직후의 행보로, 기업 가치는 9650억 달러에 달하며 경쟁사 OpenAI에 앞서 상장을 추진 중이다.",
  "TechCrunch", "https://techcrunch.com/2026/06/01/anthropic-files-to-go-public/"),
 ("엔비디아 Nemotron 3 Nano Omni, 비전·음성·언어 통합 오픈 AI 출시",
  "엔비디아가 비전·음성·언어를 하나의 아키텍처로 통합한 300억 파라미터 오픈 멀티모달 모델을 출시했다. 동급 오픈 모델 대비 최대 9배 빠른 처리량을 제공하며, 허깅페이스와 NVIDIA NIM에서 무료로 사용할 수 있다.",
  "NVIDIA Blog", "https://blogs.nvidia.com/blog/nemotron-3-nano-omni-multimodal-ai-agents/"),
 ("마이크로소프트, OpenAI 독립 선언 — 자체 AI 모델 'MAI' 시리즈 공개",
  "마이크로소프트가 Build 2026에서 OpenAI 데이터 없이 자체 훈련한 추론 모델 MAI-Thinking-1과 코딩 모델 MAI-Code-1-Flash를 발표했다. GitHub Copilot에 즉시 통합되며 Claude Haiku 4.5를 가격 대비 성능에서 앞선다.",
  "TechTimes", "https://www.techtimes.com/articles/317631/20260602/microsoft-build-2026-mai-thinking-1-first-house-reasoning-model-trained-without-openai-data.htm"),
 ("트럼프, AI 혁신·안보 행정명령 서명 — 30일 전 사전 제출 프레임워크",
  "6월 2일 트럼프 대통령이 'AI 혁신 및 안보 촉진' 행정명령에 서명했다. AI 기업이 프런티어 모델을 공개 30일 전 연방정부에 자발적으로 제출하도록 유도하며, 강제 규제보다 산업 자율 협력을 강조했다.",
  "The White House", "https://www.whitehouse.gov/presidential-actions/2026/06/promoting-advanced-artificial-intelligence-innovation-and-security/"),
 ("구글 Gemini 앱, Daily Brief·Gemini Spark 에이전트 탑재",
  "Google I/O 2026에서 구글이 Gemini 앱을 AI 허브로 전면 업그레이드했다. 아침 맞춤 요약 'Daily Brief'와 Gmail·Docs를 자율 관리하는 24/7 에이전트 'Gemini Spark'를 추가하며 ChatGPT·Claude에 정면 도전장을 냈다.",
  "TechCrunch", "https://techcrunch.com/2026/05/19/google-updates-its-gemini-app-to-take-on-chatgpt-and-claude-at-io-2026/"),
 ("메타, AI 재편에 8천 명 해고 — 최대 1450억 달러 인프라 투자 가속",
  "메타가 전체 인력의 10%인 8,000명을 감원하고 7,000명을 AI 전담팀으로 재배치했다. 2026년 AI 인프라에 최대 1,450억 달러를 투자하기 위한 조치로, 2022~23년 '효율의 해' 이후 최대 규모 구조조정이다.",
  "The Next Web", "https://thenextweb.com/news/meta-layoffs-8000-zuckerberg-ai-reality-may-2026"),
 ("안드로이드 Chrome에 Gemini Intelligence — 실시간 웹 요약 6월 배포",
  "구글이 안드로이드 기기의 Chrome 브라우저에 Gemini Intelligence를 6월 말부터 순차 배포한다. 웹 페이지를 탐색하며 실시간 요약·비교·검색이 가능해지며, AI가 맥락을 파악해 자동으로 대응한다.",
  "Google Blog", "https://blog.google/products-and-platforms/platforms/android/gemini-intelligence/"),
]

DESIGN = [
 ("제록스, Lexmark 합병 반영한 새 로고·브랜드 아이덴티티 공개",
  "제록스가 6월 Lexmark 인수를 반영해 새 브랜드 아이덴티티를 공개했다. 소문자 'xerox' 워드마크와 X를 형상화한 구형 심볼을 도입, 단순 출력 기업을 넘어 통합 워크플레이스 솔루션 기업으로의 도약을 선언했다.",
  "Actionable Intelligence", "https://www.action-intell.com/2026/06/09/xerox-debuts-new-logo/"),
 ("피그마 Make, 로컬 코드베이스 직접 연결 베타 — 디자인-개발 경계 해체",
  "5월 28일 피그마 Make가 사용자의 로컬 코드베이스와 직접 연결해 AI가 코드를 수정하는 기능을 베타로 공개했다. 주석·채팅·PR 생성 기능도 포함되며, 디자인-개발 핸드오프의 경계가 사실상 사라지고 있다.",
  "Figma Release Notes", "https://www.figma.com/release-notes/"),
 ("DesignRush 6월 어워드: 미니멀 로고·확장 가능한 아이덴티티 시스템 수상",
  "DesignRush가 6월 디자인 어워드 수상작을 발표했다. Studio AIO의 Balance 미니멀 로고가 최우수 로고상, The Click의 임페리얼 칼리지 런던 'Creative I' 확장형 시스템이 인쇄 부문상을 수상했다.",
  "DesignRush", "https://news.designrush.com/designrush-design-award-winners-june-2026"),
 ("인터브랜드, 60년 역사 첫 인하우스 리브랜드 단행",
  "세계 최대 브랜드 컨설팅사 인터브랜드가 처음으로 자체 비주얼 아이덴티티를 인하우스로 전면 재설계했다. 브랜드 전략과 디자인의 통합을 상징하는 새 시스템이 Brand New에 공개되며 업계의 주목을 받고 있다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_interbrand_done_in_house_2026.php"),
 ("항공권 앱 Going, DesignStudio와 함께 새 이름·로고·아이덴티티 공개",
  "Scott's Cheap Flights에서 이름을 바꾼 Going이 DesignStudio와 협업해 새 시각 아이덴티티를 공개했다. 브랜드명에 맞게 모션 퍼스트로 설계된 동적 로고 시스템이 도입됐다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_name_logo_and_identity_for_going_by_designstudio.php"),
 ("Creative Boom: 'AI 획일화'에 맞서는 '이상함'이 브랜딩을 구한다",
  "Creative Boom은 2026년 브랜딩 트렌드 분석에서 과잉 최적화된 AI 브랜드에 반기를 드는 독창적·실험적 비주얼이 주목받고 있다고 진단했다. 개성과 기이함이 차별화 전략으로 부상하는 역설적 흐름이다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("Fold7Design, '영국 독서의 해 2026' 캠페인 아이덴티티 공개",
  "Fold7Design이 영국 'National Year of Reading 2026' 캠페인을 위한 시각 아이덴티티를 디자인했다. 다양한 연령대가 접근 가능한 친근한 타이포그래피 시스템으로 독서 문화 확산을 목표로 한다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_national_year_of_reading_2026_by_fold7design.php"),
]

MARKETING = [
 ("아디다스 '백야드 레전즈' — 샬라메·메시가 함께한 월드컵 캠페인",
  "아디다스가 FIFA 월드컵 2026을 위해 티모테 샬라메·리오넬 메시·주드 벨링엄·데이비드 베컴 등이 등장하는 5분짜리 단편 영화 'Backyard Legends'를 공개했다. Wieden+Kennedy 제작으로 자유로운 축구 정신과 자기 믿음을 담았다.",
  "Campaign Live", "https://www.campaignlive.com/article/adidas-drops-world-cup-film-starring-timothee-chalamet/1957410"),
 ("코카콜라 'All the Feels' — 반 헤일런 'Jump' 리믹스로 월드컵 감성 공략",
  "코카콜라가 FIFA 월드컵 2026 공식 캠페인 'All the Feels'의 마지막 편 'No Better Feeling'을 공개했다. 180개 시장에 걸쳐 J Balvin·Amber Mark가 재해석한 반 헤일런 'Jump'를 앤덤으로 팬들의 감정적 순간을 담았다.",
  "Adweek", "https://www.adweek.com/creativity/coca-cola-captures-all-the-feels-of-soccer-fans-for-world-cup-2026/"),
 ("나이키 'Rip the Script' — 샬라메·로나우두·킴 카다시안 총출동",
  "나이키가 월드컵 2026을 위한 6분짜리 단편 'Rip the Script'를 공개했다. 트래비스 스콧·크리스티아누 호나우두·킴 카다시안 등 30여 명의 글로벌 스타가 등장하며, 아디다스와의 월드컵 광고 대결을 펼치고 있다.",
  "Ad Age", "https://adage.com/creativity/creative-strategy-tactics/aa-nike-world-cup-2026/"),
 ("맥도날드, 역대 최대 월드컵 캠페인 — 베컴·호나우지뉴 한 화면에",
  "맥도날드가 베컴·호나우지뉴·티에리 앙리·손흥민 등 레전드를 총동원한 역대 최대 FIFA 월드컵 캠페인을 100여 개국에 전개했다. 한정판 금빛 패키지의 빅맥 소스와 9종 콜렉터블 컵도 함께 출시됐다.",
  "Adweek", "https://www.adweek.com/creativity/mcdonalds-mounts-its-largest-ever-world-cup-push-with-a-roster-of-soccer-legends/"),
 ("메타, Ads AI Connectors 오픈 베타 — ChatGPT·Claude로 광고 관리",
  "메타가 광고주가 ChatGPT·Claude 등 외부 AI 도구로 메타 광고를 직접 관리할 수 있는 'Ads AI Connectors'를 오픈 베타로 출시했다. API 키·코딩 없이 MCP 연동만으로 캠페인 인사이트와 자연어 관리가 가능하다.",
  "Digiday", "https://digiday.com/marketing/meta-opens-its-ad-ecosystem-to-third-party-ai-tools/"),
 ("Google Marketing Live 2026 — Gemini로 재편된 광고 스택 전면 공개",
  "구글이 마케팅 라이브 2026에서 Gemini 기반의 AI 광고 솔루션을 대거 공개했다. 구글·유튜브 전 채널에 AI가 통합되며, 메타·애플도 AI 광고 도구를 고도화하며 마케터의 AI 의존도가 급속히 높아지고 있다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("월드컵 마케팅 붐 — 글로벌 브랜드 Q2 광고비 105억 달러 추가 투입",
  "WARC Media에 따르면 FIFA 월드컵 2026을 계기로 글로벌 브랜드들이 2026년 2분기에만 105억 달러의 추가 광고비를 집행할 전망이다. 월드컵 첫 북미 개최로 주요 브랜드들이 역대 최대 투자를 단행하고 있다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-brands-are-taking-the-marketing-pitch-for-the-world-cup/818434/"),
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
