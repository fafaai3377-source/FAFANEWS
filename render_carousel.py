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
 ("스트라이프, AI 게이트웨이 스타트업 오픈라우터 70억 달러대 인수",
  "스트라이프가 다양한 AI 모델을 고르게 해주는 게이트웨이 스타트업 오픈라우터를 70억 달러 이상에 인수하기로 했다고 블룸버그가 보도했다. 오픈라우터는 지난 5월 13억 달러 밸류에이션으로 1억 1,300만 달러 시리즈B를 유치한 바 있으며, 현재 800만 명 사용자와 400개 이상 모델 접근을 제공한다.",
  "TechCrunch", "https://techcrunch.com/2026/08/16/stripe-will-reportedly-acquire-ai-gateway-startup-openrouter-for-7b/"),
 ("앤트로픽 CEO 아모데이 'AI backlash는 신뢰의 위기'",
  "다리오 아모데이 앤트로픽 CEO는 자신의 AI 위험 경고가 업계 전반의 반발을 부추겼다는 투자자 개빈 베이커의 비판에 반박했다. 그는 대중의 부정적 인식이 리더들의 경고 때문이 아니라 기업과 정부에 대한 근본적인 불신에서 비롯된다고 주장하며, AI 기업이 아직 큰 약속을 이행하지 못했다는 비판이 가장 타당하다고 인정했다.",
  "TechCrunch", "https://techcrunch.com/2026/08/16/anthropic-ceo-says-ai-backlash-is-fundamentally-a-crisis-of-trust/"),
 ("딥시크, V4 모델 API 가격 최대 1,100% 인상",
  "딥시크가 8월 16일부터 V4-Flash와 V4-Pro의 API 가격을 모델과 시간대에 따라 50%에서 1,100% 이상 인상한다. 피크(UTC 01~04시, 06~10시)와 비피크 시간대로 나눠 요금을 차등화했으며, 회사는 자원 배분을 합리화하기 위한 조치라고 설명했지만 인상 후에도 경쟁사 대비 여전히 저렴한 수준이다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/deepseek-raising-api-prices-1-174027670.html"),
 ("xAI, 그록 관련 미성년자 이미지 남용 소송 확대",
  "계부가 xAI의 챗봇 그록을 이용해 11세 때 사진을 조작했다고 주장하는 여성이 xAI를 상대로 한 소송에 새로 합류했다. 테네시주 청소년 3명이 이미 유사한 소송을 진행 중이며, xAI가 미성년자 이미지 악용을 막을 기본 안전장치를 갖추지 않았다는 지적이 제기되고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/08/15/woman-claims-her-stepfather-used-grok-to-transform-childhood-photo-into-explicit-imagery/"),
 ("구글, 코딩·AI 에이전트용 제미나이 3.7 플래시 출시",
  "구글이 전작 출시 3주 만에 엔트리급 모델 제미나이 3.7 플래시를 공개하며 앤트로픽·오픈AI 동급 모델을 9개 벤치마크에서 앞선다고 밝혔다. 코딩 벤치마크 FrontierCode 1.1에서 1위를 기록했고 프롬프트당 최대 100만 토큰 입력을 지원하며, 연말까지 이전 모델 대비 가격을 50% 낮춰 제공한다.",
  "SiliconANGLE", "https://siliconangle.com/2026/08/13/google-launches-gemini-3-7-flash-coding-ai-agent-projects/"),
 ("스캔AI, 기업용 AI 에이전트 플랫폼과 함께 6,300만 달러 시리즈C 유치",
  "업무 관찰 기반 AI 스타트업 스캔AI가 케이시 이노베이션과 델 테크놀로지스 캐피탈 주도로 6,300만 달러 시리즈C를 유치하며 'Blueprint·Intelligence·Agents' 세 가지 기업용 AI 제품을 정식 출시했다. 회사는 실제 업무 방식을 관찰해 AI 에이전트에 맥락을 제공하는 방식으로 포춘 50대 기업 중 25%, 미국 상위 10대 은행 중 7곳을 고객으로 확보했다고 밝혔다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/skan-ai-raises-63-million-120000480.html"),
 ("저커버그의 'AI 미래' 선언, 대중의 신뢰 얻지 못해",
  "마크 저커버그 메타 CEO가 6,500단어 분량의 에세이 'The Future is for Everyone'을 통해 개인용 AI 에이전트가 삶을 개선하는 미래상을 제시했지만 회의적 반응에 부딪혔다. 과거 '연결'을 약속했던 메타가 실제로는 광고와 분노 유발 콘텐츠를 낳았다는 신뢰 문제와, 메타가 프런티어 AI 경쟁에서 밀려 개인용 AI로 전략을 선회하고 있다는 지적이 제기됐다.",
  "TechCrunch", "https://techcrunch.com/2026/08/16/why-people-arent-buying-mark-zuckerbergs-ai-future/"),
]

DESIGN = [
 ("인스타그램, 10년 만의 브랜드 리프레시 단행",
  "인스타그램이 2016년 이후 처음으로 워드마크와 브랜드 시스템 전반을 새로 다듬었다. 기존 스크립트 로고의 정체성은 유지하되 더 절제되고 자신감 있는 형태로 다듬었고, 손글씨체 'Instagram Pen'과 모노스페이스체 'Instagram Sans Mono'를 새로 추가했다.",
  "It's Nice That", "https://www.itsnicethat.com/features/behind-instagrams-first-major-refresh-in-10-years-partnership-130826"),
 ("피그마, AI 시대 '속도보다 방향'을 말하다",
  "피그마 개발자 애드보킷 제이크 알보가 AI 시대 소프트웨어 제작에서 실행 속도와 판단력의 균형을 다룬 글을 게재했다. AI는 실행 속도를 높이지만 사고의 명료함을 대신해주지 않으므로 팀이 결과물을 무비판적으로 수용하는 '인지적 항복'을 경계해야 한다고 강조한다.",
  "Figma Blog", "https://www.figma.com/blog/how-to-move-fast-toward-the-right-thing/"),
 ("피그마, 커뮤니티 제작 디자인 에이전트 '스킬' 10선 공개",
  "피그마가 디자인 에이전트용으로 커뮤니티가 만든 스킬(마크다운 기반 지침 파일) 10가지를 소개했다. 비주얼 이펙트, 모션 디자인, 인터페이스 품질 점검, 컴포넌트 문서화, 디자인 리뷰 등 디자이너의 판단 기준을 담은 스킬들이 포함됐다.",
  "Figma Blog", "https://www.figma.com/blog/try-these-10-skills-and-show-off-your-own/"),
 ("원플러스 스튜디오, '충돌'에서 나오는 창작 방식 조명",
  "대학 동창 4인이 설립한 크리에이티브 스튜디오 원플러스가 서로 다른 취향과 배경을 충돌시켜 협업하는 작업 방식을 소개했다. 하나의 아이디어가 팀원 네 명의 서로 다른 시각을 모두 통과해야 살아남는다는 원칙을 지킨다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/how-oneplus-studio-was-built-on-discovery-exchange-and-a-bit-of-healthy-friction/"),
 ("올리브오일 브랜드 어니스트 토일, 일러스트레이터 기용 전략으로 성공",
  "그리스 올리브오일 브랜드 어니스트 토일이 고정된 하나의 아이덴티티 대신 매 수확 시즌마다 여러 일러스트레이터에게 패키지 디자인을 맡기는 전략을 택했다. 이 브랜드는 현재 오토렝기, 더 더스티 너클 등 주요 매장에 입점했다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/honest-toil-bet-its-olive-oil-brand-on-illustrators-and-it-paid-off/"),
 ("피그마, 'Figma Make' 생산성 효과 자체 검증",
  "피그마 데이터사이언스팀이 참가자 100명을 대상으로 무작위 대조 실험을 진행해 AI 디자인 도구 'Figma Make'의 시간 절감 효과를 측정했다. 작업 속도가 20% 빨라지고 체감 난이도는 16% 낮아졌으며, 프로덕트 매니저는 단순 작업에서 최대 23% 빠른 효과를 봤다.",
  "Figma Blog", "https://www.figma.com/blog/measuring-time-savings-from-figma-make/"),
 ("브라질 펫브랜드 파타, 로티파와 새 아이덴티티 공개",
  "브라질 반려동물 용품 브랜드 파타가 디자인 스튜디오 로티파와 함께 새 로고와 아이덴티티를 선보였다. 검정과 노랑을 주조색으로 한 워드마크 중심 시스템으로 그리드 패턴과 굵은 컬러 블록을 활용해 반려동물 제품 특유의 활동성을 표현했다.",
  "Brand New (UnderConsideration)", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_patta_by_lotipa.php"),
]

MARKETING = [
 ("애드롤, 챗GPT 광고 파일럿 프로그램 시작",
  "애드롤이 일부 고객을 대상으로 챗GPT 내 광고를 테스트하는 파일럿 프로그램을 시작했다. 생성형 AI 챗봇 응답 하단에 스폰서 광고를 노출해 소비자가 제품을 탐색하는 순간을 공략하는 방식이다.",
  "MarTech Series", "https://martechseries.com/sales-marketing/programmatic-buying/adroll-expands-multichannel-advertising-capabilities-through-chatgpt-ads-pilot/"),
 ("피터 잉글랜드, Z세대 겨냥 서브 브랜드 'VYBE' 론칭",
  "인도 남성복 브랜드 피터 잉글랜드가 Z세대를 겨냥한 캐주얼 서브 브랜드 'VYBE'를 출시했다. 오길비가 기획한 'VYBE Verified' 캠페인은 인디 아티스트들을 내세워 자기 확신이라는 메시지를 음악 중심으로 전달했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/peter-england-launches-gen-z-focused-brand-vybe-12257306"),
 ("어반아웃피터스, 첫 CTV 광고로 캠퍼스 라이프 공략",
  "어반아웃피터스가 러트거스대학교에서 촬영한 첫 커넥티드TV 광고 'All Together Now'를 공개했다. 76명의 대학생이 출연해 여러 산하 브랜드 제품을 착용하며 개학 시즌 Z세대 공략에 나섰다.",
  "Marketing Dive", "https://www.marketingdive.com/news/urban-outfitters-embraces-campus-life-in-first-ctv-commercial/827619/"),
 ("핀터레스트-질로우, 광고 타겟팅 파트너십 체결",
  "핀터레스트와 질로우가 파트너십을 맺고 질로우 엘리베이트 광고주가 핀터레스트 이용자를 타겟팅할 수 있도록 했다. 34개 주택 관련 소비자 세그먼트와 200여 개 주거 시그널 데이터를 안전하게 공유해 구매 의도가 높은 이용자에게 도달한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/sociable-pinterest-partners-with-zillow-for-ad-targeting/827812/"),
 ("WPP, '엘리베이트28' 전략으로 매출 감소폭 축소",
  "글로벌 광고 지주사 WPP가 추진 중인 턴어라운드 전략 '엘리베이트28'이 초기 성과를 내고 있다. 동일 기준 매출 감소율이 1분기 6.7%에서 2분기 2.8%로 축소됐고, 웬디스·에스티로더·하이네켄 등 신규 고객사를 확보했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/wpps-streamlined-strategy-secures-new-business-as-revenue-declines-narrow/827443/"),
 ("갭, 가을 데님 캠페인에 '옵세션' 배우 인데 나바레트 기용",
  "갭이 가을 데님 캠페인 'Denim on your own'을 공개하며 드라마 '옵세션'으로 주목받은 배우 인데 나바레트와 싱어송라이터 맬컴 토드를 내세웠다. 타임스스퀘어 옥외광고를 포함해 디지털·소셜·매장 전 채널에 노출된다.",
  "Marketing Dive", "https://www.marketingdive.com/news/gaps-fall-campaign-enlists-obsession-star-to-promote-denim/827375/"),
 ("스위트스팟, 오프라인 브랜드 전략 공유하는 'SPIN-OFF 26' 개최",
  "팝업·상업공간 전문기업 스위트스팟이 오는 8월 25일 서울 익선동에서 오프라인 마케팅 인사이트 세션 'SPIN-OFF 26'을 연다. 시디즈와 메이커스마크 실무자가 일회성 팝업을 반복 가능한 브랜드 자산으로 만드는 노하우를 공유한다.",
  "벤처스퀘어", "https://www.venturesquare.net/1106085"),
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
