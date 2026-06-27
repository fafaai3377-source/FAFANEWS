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
 ("xAI Grok 4.3, Amazon Bedrock 출시 — 최저 환각률·100만 토큰 컨텍스트",
  "Grok 4.3이 Amazon Bedrock에서 정식 출시됐다. 100만 토큰 컨텍스트 윈도우와 구성 가능한 추론 수준을 제공하며, 프런티어 모델 중 최저 비용으로 Omniscience 벤치마크 1위에 올랐다.",
  "Artificial Analysis", "https://artificialanalysis.ai/articles/xai-launches-grok-4-3-with-improved-agentic-performance-and-lower-pricing"),
 ("NVIDIA NemoClaw — DGX Spark용 오픈소스 에이전트 배포 환경 공개",
  "엔비디아가 6월 DGX Spark 플랫폼용 오픈소스 에이전트 배포 환경 NemoClaw를 공개했다. 최적화 로컬 모델·에이전트 오케스트레이션 하네스·보안 샌드박스 게이트웨이를 하나의 패키지로 제공한다.",
  "devFlokers", "https://www.devflokers.com/blog/ai-news-june-2026-models-research-developments"),
 ("마이크로소프트 MAI-Image-2.5, 텍스트→이미지 리더보드 3위 — Copilot 내장",
  "마이크로소프트가 자체 개발한 MAI-Image-2.5를 6월 출시하고 Copilot에 네이티브 통합했다. 주요 텍스트-이미지 생성 벤치마크에서 3위를 기록하며 멀티모달 경쟁에 본격 참전했다.",
  "devFlokers", "https://www.devflokers.com/blog/ai-tech-news-model-releases-june-2026"),
 ("Gartner: 2026년 전 세계 AI 지출 2.59조 달러, 전년比 47% 급증",
  "가트너가 2026년 전 세계 AI 관련 지출이 2조 5900억 달러에 달할 것으로 전망했다. AI가 기업 IT 예산의 핵심으로 자리잡으면서 소프트웨어·인프라·서비스 전 부문에서 이례적인 지출 증가가 이어지고 있다.",
  "Gartner", "https://www.gartner.com/en/newsroom/press-releases/2026-05-19-gartner-forecasts-worldwide-ai-spending-to-grow-47-percent-in-2026"),
 ("Legora, AI 법률 워크스페이스에 5.5억 달러 시리즈 D — 밸류 55억 달러",
  "스웨덴 법률 AI 스타트업 레고라가 Accel 주도로 5억 5000만 달러 시리즈 D를 완료했다. 밸류에이션이 이전 라운드 대비 세 배인 55억 5000만 달러로 뛰었으며, 미국 시장 확장에 집중할 계획이다.",
  "Legora", "https://legora.com/newsroom/legora-raises-550-million-series-d-to-fuel-us-growth"),
 ("Arcade AI, 엔터프라이즈 에이전트 거버넌스 플랫폼으로 6000만 달러 시리즈 A",
  "AI 에이전트가 프로덕션에서 수행할 수 있는 행동을 기업이 제어하는 보안 레이어를 개발한 Arcade가 6000만 달러 시리즈 A를 조달했다. 에이전트 시대 기업 거버넌스 수요가 VC 투자를 끌어모으고 있다.",
  "Crescendo AI", "https://www.crescendo.ai/news/latest-vc-investment-deals-in-ai-startups"),
 ("Qualcomm, AI 반도체 스타트업 텐스토런트 80~100억 달러 인수 협상 중",
  "퀄컴이 짐 켈러가 설립한 AI 반도체 스타트업 텐스토런트를 약 80억~100억 달러에 인수하는 협상을 진행 중인 것으로 알려졌다. 엣지 AI 인프라 경쟁이 심화되는 가운데 반도체 대형 M&A의 서막으로 해석된다.",
  "Crescendo AI", "https://www.crescendo.ai/news/latest-ai-news-and-updates"),
]

DESIGN = [
 ("Figma Config 2026: 인텔리전트 캔버스·모션 타임라인·코드 레이어 전격 공개",
  "6월 23~25일 Config 2026에서 피그마가 인텔리전트 캔버스를 발표했다. AI 모션 생성·코드 레이어 GitHub 동기화·WebGPU 셰이더·Weave 미디어 툴이 대거 추가되며 디자인 툴의 전면 확장이 이뤄졌다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("Figma AI 에이전트 Skills & Connectors — Notion·Slack·GitHub 팀 워크플로우 연결",
  "피그마 AI 에이전트가 커스텀 Skills와 Connectors 기능을 탑재했다. 팀이 워크플로우를 재사용 가능한 명령으로 패키징하고 Notion·Slack·GitHub 등 외부 툴과 직접 연결해 자동 업데이트를 주고받을 수 있다.",
  "explainx.ai", "https://explainx.ai/blog/figma-config-2026-complete-recap-motion-code-shaders-ai-2026"),
 ("Spotify 20주년 디스코볼 로고 공개 — '디스코모피즘' 트렌드 촉발",
  "스포티파이가 창립 20주년을 맞아 기존 녹색 원형 로고를 거울 디스코볼 형태의 한정 디자인으로 교체했다. 깊이·그라데이션·질감을 강조한 이 변화가 '디스코모피즘'이라는 새로운 디자인 트렌드 논의를 불러일으켰다.",
  "Spotify Newsroom", "https://newsroom.spotify.com/2026-04-23/spotify-design-history/"),
 ("BBH, 44년 만의 첫 비주얼 아이덴티티 개편 — Studio DRAMA와 전용 서체 제작",
  "광고 에이전시 BBH가 1982년 창립 이래 처음으로 대규모 비주얼 아이덴티티를 개편했다. Studio DRAMA와 협업해 창업자들의 개성을 담은 전용 서체를 개발하며 'AI 획일화'에 반기를 들었다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/bbh-studio-drama-rebrand-graphic-design-project-260226"),
 ("2026~2027 UI 디자인 방향: 깊이·질감·의도적 불완전함의 귀환",
  "디자이너 미칼 말레비치가 2026~2027 UI 트렌드를 분석했다. 플랫 디자인에서 벗어나 깊이·질감·마이크로인터랙션이 복귀하고, AI 생성 무결점 디자인에 대한 반발로 '의도적 불완전함'이 차별점으로 부상한다는 시각이다.",
  "Medium / Michal Malewicz", "https://michalmalewicz.medium.com/ui-design-direction-2026-2027-2b4b6eb88336"),
 ("2026 브랜드 아이덴티티 트렌드: 인간화·유기적 텍스처·목적 주도 브랜딩",
  "2026년 브랜드 아이덴티티 디자인은 손으로 그린 타이포그래피·유기적 텍스처·표현적 사진을 통한 '인간화'를 핵심으로 한다. 목적 주도 브랜딩이 필수가 되면서 대형 리브랜드보다 '정제된 리프레시'가 선호되고 있다.",
  "KOTA", "https://kota.co.uk/blog/branding-inspiration-brand-design-trends-for-2026"),
 ("2026 UX 설계 10대 전환: 마이크로인터랙션 복귀·사운드 디자인 시스템 도입",
  "2026년 주목해야 할 UX 변화 10선으로 마이크로인터랙션의 귀환과 사운드 디자인 시스템 도입이 꼽힌다. 인터페이스가 단순 시각을 넘어 촉각적·청각적 경험으로 진화하면서 설계 방식 자체가 달라지고 있다.",
  "UX Collective", "https://uxdesign.cc/10-ux-design-shifts-you-cant-ignore-in-2026-8f0da1c6741d"),
]

MARKETING = [
 ("McDonald's, 비밀 메뉴 공식화 — 자폭 광고판·Metro 파트너십으로 화제",
  "맥도날드가 수십 년간 팬들 사이에 전해지던 '비밀 메뉴'를 공식 상품으로 출시했다. Leo UK 주도로 자폭 광고판·Metro 파트너십 등 파격적인 캠페인을 전개해 큰 화제를 모았다.",
  "The Drum", "https://www.thedrum.com/news/mcdonald-s-makes-its-secret-menu-official"),
 ("Charli XCX, Nothing 첫 글로벌 앰배서더 & 주주 — 헤드폰 48시간 완판",
  "팝스타 찰리 XCX가 테크 브랜드 Nothing의 첫 글로벌 앰배서더이자 주주로 합류했다. 135시간 배터리 헤드폰을 위한 저예산 캠페인을 찍었고, 영국·서유럽에서 출시 48시간 만에 전량 완판됐다.",
  "Hypebae", "https://hypebae.com/2026/5/charli-xcx-nothing-tech-brand-ambassador-campaign-headphones-where-to-buy"),
 ("2026 최고 캠페인: Heinz KegChup·Corona 레이저 라임·Cadbury GooTool",
  "Famous Campaigns가 선정한 2026 상반기 최고 캠페인에 Heinz KegChup·Corona 레이저 각인 라임·Cadbury GooTool이 이름을 올렸다. 팬들이 실제로 원하는 문제를 해결하는 실용적 캠페인이 두각을 나타냈다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/06/the-best-brand-campaigns-of-2026-so-far/"),
 ("TikTok, 칸 라이언즈서 'Symphony AI Agent' 발표 — AI 크리에이터 매칭 시대",
  "TikTok이 칸 라이언즈 2026에서 Symphony AI Agent를 공개했다. 광고주의 캠페인 목표를 플랫폼 인사이트·트렌드와 결합해 크리에이터를 자동 매칭하는 기능으로, AI가 인플루언서 마케팅의 핵심 도구로 부상하고 있다.",
  "eMarketer", "https://www.emarketer.com/content/tiktok-s-cannes-update-puts-ai-heart-of-influencer-marketing"),
 ("2026년 6월 마케팅 트렌드: AI 연결성·진정성 콘텐츠·플랫폼 다변화",
  "6월 마케팅 핵심 트렌드는 AI 광고 연결성·진정성 콘텐츠 선호·플랫폼 다변화다. AI 생성 콘텐츠의 범람 속에서 '진짜처럼 보이는 의도적 콘텐츠'가 소비자 신뢰를 얻는 새 기준이 되고 있다.",
  "Seafoam Media", "https://seafoammedia.com/june-2026-marketing-news-trends-insights/"),
 ("2026 월간 마케팅 리뷰 — 팬 참여·실용 혁신이 광고를 대체하다",
  "2026 월간 마케팅 리뷰에 따르면 바이럴 광고보다 팬 참여·실용적 혁신 캠페인이 더 오래, 더 멀리 퍼진다. 브랜드가 팬 문화를 직접 제품에 통합한 '아이코노믹스' 접근법이 트렌드로 떠올랐다.",
  "The Gone Network", "https://www.thegonetwork.com/articles/the-best-marketing-campaigns-of-2026---monthly-review-2026"),
 ("2026 마케팅 예측 27선: AI·크리에이터 경제·리테일 미디어의 교차점",
  "2026 마케팅 27대 트렌드에서 AI 개인화·크리에이터 경제·리테일 미디어가 핵심 키워드로 꼽혔다. 통합 전략 없이 개별 채널에만 집중하는 브랜드는 AI 시대에 도태될 위험이 높다는 경고도 담겼다.",
  "Quad", "https://www.quad.com/insights/27-marketing-trends-and-predictions-for-2026"),
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
