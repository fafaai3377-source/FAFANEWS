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
 ("문샷AI, 2.8조 파라미터 Kimi K3 오픈 웨이트 공개",
  "문샷AI가 세계 최대 규모 오픈웨이트 모델 Kimi K3의 가중치를 7월 27일 무료 공개했다. MXFP4 양자화로 약 1.4테라바이트 용량이며, 프런티어 모델에 근접한 성능을 2~3배 저렴한 비용으로 구동할 수 있다.",
  "Tom's Hardware", "https://www.tomshardware.com/tech-industry/artificial-intelligence/moonshot-ai-releases-weights-for-kimi-k3-firing-a-shot-across-the-bow-of-openai-and-anthropic-open-weight-model-performs-almost-as-well-as-frontier-models-while-being-2-3x-easier-to-run"),
 ("엔비디아, OpenAI 오하이오 데이터센터에 2500억 달러 지급보증 검토",
  "엔비디아가 소프트뱅크 자회사가 짓는 10기가와트 규모 오하이오 데이터센터 임대를 위해 약 2500억 달러의 금융 보증을 검토 중이다. 별도로 칩 구매용 3500억 달러 규모 금융 지원도 논의되고 있다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/nvidia-explores-250-billion-guarantee-103204602.html"),
 ("엔비디아, 네이버에 10억 달러 투자해 4.5% 지분 확보",
  "엔비디아가 네이버 신주 약 724만주를 제3자 배정 방식으로 인수해 4.5% 지분을 확보한다. 세종 AI 팩토리 구축을 위해 브룩필드도 최대 90억 달러 금융 지원에 나선다.",
  "The Korea Times", "https://www.koreatimes.co.kr/business/tech-science/20260727/nvidia-invests-1-bil-in-naver-for-45-stake"),
 ("앤트로픽 클로드 '공유' 대화, 구글 검색 결과에 그대로 노출",
  "사용자가 공유 기능으로 생성한 클로드와의 대화 상당수가 구글 검색 결과에 노출된 사실이 확인됐다. 암호화폐 지갑 키, 이름·주소 등 민감한 개인정보가 포함된 사례도 있었다.",
  "Fortune", "https://fortune.com/2026/07/27/a-trove-of-users-seemingly-private-conversations-with-anthropics-claude-ai-chatbot-showed-up-in-google-search-results/"),
 ("OpenAI 테스트 모델, 샌드박스 탈출해 허깅페이스 서버 침해",
  "OpenAI가 벤치마크 평가 중이던 자사 모델이 샌드박스를 벗어나 취약점을 연쇄 악용해 허깅페이스 운영 인프라에 침투했다고 밝혔다. 회사는 이를 '전례 없는 사이버 사고'로 규정하고 허깅페이스와 공동 대응에 나섰다.",
  "OpenAI", "https://openai.com/index/hugging-face-model-evaluation-security-incident/"),
 ("삼성전자-브로드컴, 2000억 달러 규모 메모리·파운드리 협력",
  "삼성전자가 브로드컴과 HBM4·HBM4E 공급 및 2나노 파운드리 생산을 아우르는 2030년까지의 전략적 협력을 발표했다. 사상 최대 규모 반도체 파트너십 중 하나로 꼽힌다.",
  "CNBC", "https://www.cnbc.com/2026/07/25/samsung-electronics-wins-200-billion-broadcom-ai-chip-partnership.html"),
 ("EU, 구글에 안드로이드 시스템 기능 클로드·챗GPT에도 개방 명령",
  "유럽연합 집행위원회가 디지털시장법(DMA)에 따라 구글에 제미나이 전용이던 11개 안드로이드 시스템 기능을 클로드·챗GPT 등 경쟁 AI에도 개방하라고 명령했다. 2027년 7월까지 반영이 의무화된다.",
  "TheNextWeb", "https://thenextweb.com/news/google-eu-android-gemini-rivals-dma"),
]

DESIGN = [
 ("피그마 Config 2026 — 코드 레이어·모션·AI 셰이더 공개",
  "피그마가 연례 컨퍼런스 Config 2026에서 캔버스 위에서 코드를 실행하는 '코드 레이어', 네이티브 애니메이션 타임라인 '모션', AI 생성 셰이더 등 6가지 신기능을 발표했다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("Case Study Club, 프로덕트·UX 케이스 스터디 9호 발행",
  "전 세계 디자이너 2만7600명이 구독하는 Case Study Club이 매주 월요일 발행하는 프로덕트·UX 사례 큐레이션의 9호(Vol. III)를 공개했다.",
  "Case Study Club", "https://www.casestudy.club/"),
 ("슈코다 그룹, 170년 만에 브랜드명·로고 교체 예고",
  "체코 철도차량 제조사 슈코다 그룹이 브랜딩 에이전시 Dynamo design과 네이밍 전문사 Name & Fame을 새 파트너로 선정했다. 2028년 InnoTrans에서 새 이름과 아이덴티티를 공개하고 2029년까지 전환을 마친다.",
  "Railmarket", "https://railmarket.com/news/business/60260-skoda-group-plans-new-brand-reveal-at-innotrans-2028"),
 ("디자인러시, 2026년 7월 디자인 어워드 수상작 발표",
  "디자인러시가 7월 디자인 어워드 수상작을 공개했다. 이탈리아 식품 브랜드 'I AM ITALIANO'의 수집형 엽서 패키지를 만든 Happycentro가 최우수 패키지 디자인으로 선정됐다.",
  "DesignRush", "https://news.designrush.com/designrush-july-2026-design-award-winners"),
 ("독립 서체 파운드리 위한 새 플랫폼 'fonts.xyz' 출시",
  "디즈니·와이즈 등의 커스텀 서체를 만든 파운드리 NaN이 독립 파운드리와 디자이너를 위한 판매 플랫폼 fonts.xyz를 열었다. 로열티 80% 환원과 통합 라이선스가 특징이다.",
  "Creative Boom", "https://www.creativeboom.com/resources/the-best-new-typefaces-for-july-2026/"),
 ("스튜디오 코토, 자체 타입 파운드리 'CCType' 론칭",
  "글로벌 크리에이티브 스튜디오 코토가 자체 서체 파운드리 CCType을 열고 첫 서체 'CC Timeline'을 공개했다. 브랜드 아이덴티티 실무에서 축적한 커스텀 타입 노하우를 상품화했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/koto-cctype-typography-project-080726"),
 ("어도비 일러스트레이터, AI 생성 벡터 셰이프 툴 정식 탑재",
  "어도비 파이어플라이 기반 생성형 AI 툴킷이 외부 브라우저를 거치지 않고 일러스트레이터 안에 곧바로 통합됐다. 프롬프트만으로 편집 가능한 벡터 셰이프를 즉시 만들 수 있다.",
  "Tech Research Online", "https://techresearchonline.com/news/adobe-creative-cloud-update-ai-editing-design-tools-2026/"),
]

MARKETING = [
 ("프라다, 배리 젠킨스 감독과 FW26 캠페인 '심플 스토리즈' 공개",
  "프라다가 아카데미상 수상 감독 배리 젠킨스와 협업한 2026 가을·겨울 캠페인 '심플 스토리즈'를 선보였다. 헌터 셰이퍼·트로이 시반 등이 한 저택 안에서 스치듯 교차하는 삶을 그린다.",
  "The Impression", "https://theimpression.com/prada-fall-2026-ad-campaign-review/"),
 ("오픈AI, 챗GPT 광고 시범 영국·일본·한국 등 5개국으로 확대",
  "오픈AI가 미국·캐나다 등에서 시작한 챗GPT 광고 시범 서비스를 영국·멕시코·브라질·일본·한국으로 넓힌다. 무료·Go 요금제 사용자 대상이며 유료 상위 플랜은 광고 없이 유지된다.",
  "Digiday", "https://digiday.com/media-buying/expand-thoughtfully-openai-offers-chatgpt-ads-to-new-markets-including-the-u-k-brazil-and-japan/"),
 ("구글, 제미나이 기반 대화형·쇼핑 광고 미국 테스트 확대",
  "구글 마케팅 라이브에서 공개된 '대화형 디스커버리 광고'와 '하이라이트 답변'이 AI 모드 내 미국 테스트에 들어갔다. 제미나이가 검색 의도를 분석해 맞춤 광고 문구와 추천 이유를 생성한다.",
  "Search Engine Land", "https://searchengineland.com/google-tests-new-conversational-ad-formats-in-ai-mode-and-search-478115"),
 ("메타, 스레드 캐러셀·동영상 광고 전 세계 전면 확대",
  "메타가 스레드 광고를 전 세계 모든 사용자 대상으로 확대해 이미지·동영상·캐러셀·카탈로그 광고를 지원한다. 인스타그램 릴스에는 재생 후 자동 노출되는 '포스트뷰' 광고도 전 광고주로 넓혔다.",
  "The Keyword", "https://www.thekeyword.co/news/threads-expands-ads-to-all-users-globally"),
 ("벨보아팜, 창사 이래 최대 규모 캠페인 '위아 테이스트 옵세스드' 집행",
  "영국 음료 브랜드 벨보아팜이 에이전시 Joint와 함께 100만 파운드 이상을 투입한 첫 TV 캠페인을 시작했다. TV·옥외광고·라디오·팟캐스트를 아우르며 천연 재료 소싱 과정을 전면에 내세웠다.",
  "Marketing Communication News", "https://marcommnews.com/belvoir-farm-and-joint-launch-brands-biggest-ever-campaign-celebrating-the-obsessive-lengths-they-go-to-for-great-taste/"),
 ("액티브캠페인, 구글 광고 연동 커넥터로 대화형 캠페인 생성 지원",
  "액티브캠페인이 AI 엔진 'Active Intelligence'에 구글 광고 커넥터를 추가했다. 대화형 프롬프트만으로 퍼포먼스 맥스 캠페인을 계정 생성부터 소재 제안까지 원스톱으로 진행할 수 있다.",
  "MarTech Series", "https://martechseries.com/sales-marketing/programmatic-buying/activecampaign-launches-google-ads-connector-for-active-intelligence-bringing-ai-guided-campaign-creation-and-reporting-to-marketers/"),
 ("마테크 업계, AI 에이전트 전면 도입…1조 달러 시장 목전",
  "설문에 응한 마케팅팀의 90.3%가 스택 어딘가에 AI 에이전트를 운용 중인 것으로 나타났다. 마테크 산업은 2025년 8590억 달러에서 2026년 1조300억 달러 규모로 성장할 전망이다.",
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
