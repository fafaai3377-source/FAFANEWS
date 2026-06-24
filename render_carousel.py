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
 ("Anthropic, Claude Fable 5 공개 — 미 수출 통제로 4일 만에 차단",
  "Anthropic이 6월 9일 가장 강력한 모델 Claude Fable 5를 공개했다. 사실상 모든 AI 벤치마크에서 최고 성능을 기록했으나 미국 정부의 수출 통제 지침으로 4일 만에 전 세계 서비스가 중단됐다.",
  "TechCrunch", "https://techcrunch.com/2026/06/09/anthropic-released-claude-fable-5-its-most-powerful-model-publicly-days-after-warning-ai-is-getting-too-dangerous/"),
 ("Google Gemini Spark — 24시간 백그라운드 개인 AI 에이전트 출시",
  "구글이 I/O 2026에서 Gemini Spark를 공개했다. Gemini Enterprise·Workspace 고객 전용 24시간 개인 AI 에이전트로, 장기 복합 태스크를 사용자 지시에 따라 백그라운드에서 자율 처리한다.",
  "Google Blog", "https://blog.google/innovation-and-ai/sundar-pichai-io-2026/"),
 ("Arcade, AI 에이전트 보안·인증 플랫폼으로 $60M 시리즈A 완료",
  "엔터프라이즈 AI 에이전트 보안 스타트업 Arcade가 SYN Ventures·모건스탠리 참여 하에 6,000만 달러 시리즈A를 완료했다. 어떤 에이전트가 어떤 시스템에서 어떤 행동을 했는지 추적·관제하는 보안 레이어를 제공한다.",
  "Arcade.dev", "https://www.arcade.dev/blog/arcade-series-a/"),
 ("Anthropic, 기업가치 9,650억 달러 — OpenAI 제치고 AI 1위",
  "Anthropic이 5월 28일 Altimeter·Sequoia 등으로부터 650억 달러 시리즈H를 조달했다. 기업가치 9,650억 달러로 처음으로 OpenAI를 제치고 세계 최고가 비상장 AI 기업에 등극했다.",
  "Anthropic", "https://www.anthropic.com/news/series-h"),
 ("DeepSeek V4, 1.6조 파라미터 MoE 모델 공개 — 저가로 프런티어 도전",
  "DeepSeek이 1.6조 파라미터 혼합전문가 모델 V4-Pro와 V4-Flash를 공개했다. 최대 100만 토큰 컨텍스트를 지원하며, 출력당 $0.87/M의 저렴한 가격으로 프런티어급 중국 모델의 가격 경쟁을 선언했다.",
  "SitePoint", "https://www.sitepoint.com/deepseek-v4-released-whats-new-in-the-latest-model-2026/"),
 ("백악관, 첨단 AI 혁신·보안 행정명령 서명",
  "트럼프 대통령이 6월 2일 '첨단 AI 혁신·보안 촉진' 행정명령에 서명했다. 프런티어 AI 모델을 공개 전 최대 30일간 정부가 사전 평가하는 자발적 절차를 도입해 AI 안보 강화와 혁신 촉진을 함께 추구한다.",
  "White House", "https://www.whitehouse.gov/presidential-actions/2026/06/promoting-advanced-artificial-intelligence-innovation-and-security/"),
 ("Q1 2026 글로벌 VC 투자 $3,000억 돌파 — AI가 전체의 33% 주도",
  "2026년 1분기 전 세계 스타트업 투자가 3,000억 달러를 넘어 역대 최고 기록을 갈아치웠다. AI 분야가 전체 VC 투자의 33%를 차지하며 급증을 주도했고, 시리즈A 평균 조달액은 5,190만 달러에 달했다.",
  "Crunchbase", "https://news.crunchbase.com/venture/record-breaking-funding-ai-global-q1-2026/"),
]

DESIGN = [
 ("제록스, Lexmark 통합 후 새 브랜드 아이덴티티 전격 공개",
  "제록스가 Lexmark 인수 통합을 기념해 새 로고와 브랜드 아이덴티티를 발표했다. 두 회사의 강점을 결합한 비주얼 시스템으로, 전통 인쇄 기업에서 복합 솔루션 기업으로의 변신을 선언했다.",
  "In-plant Impressions", "https://www.inplantimpressions.com/article/xerox-unveils-new-brand-identity/"),
 ("칸 라이언즈 2026, Design Grand Prix — Apple TV+ 리브랜드 수상",
  "칸 라이언즈 2026 Design Grand Prix를 Apple TV+의 리브랜드 프로젝트가 수상했다. 6월 24일 발표된 크래프트 부문에서 Adidas·De'Longhi의 미니어처 커피숍 등 창의성과 기술의 결합이 돋보였다.",
  "LBBOnline", "https://lbbonline.com/news/Cannes-Lions-2026-Grand-Prix-Winners-in-Entertainment-Gaming-Music-Sport-Design-Digital-Craft-Film-Craft-and-Industry-Craft"),
 ("영국 독서의 해 2026, Fold7Design이 새 아이덴티티 공개",
  "영국 Fold7Design이 National Year of Reading 2026을 위한 새 브랜드 아이덴티티를 공개했다. 책 읽는 행위 자체를 시각화한 유연하고 활기찬 로고 시스템으로 교육계와 언론의 주목을 받고 있다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_national_year_of_reading_2026_by_fold7design.php"),
 ("2026 동적 로고 시대 — 기업 50% 이상 '키네틱 브랜드' 도입",
  "정적 로고의 시대가 저물고 있다. 2026년 미국·유럽 기업의 절반 이상이 동적 로고를 채택했으며, 움직이고 상호작용하는 '살아있는 브랜드'가 아이덴티티 디자인의 새 기준이 되고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("DesignRush, 2026년 6월 디자인 어워드 수상작 발표",
  "DesignRush가 6월 웹사이트·로고·인쇄·앱·패키징·영상 6개 부문 수상작을 발표했다. Qodeca의 포트폴리오 리디자인과 Studio AIO의 'Balance' 로고 시스템이 특히 혁신성을 인정받았다.",
  "DesignRush", "https://news.designrush.com/designrush-design-award-winners-june-2026"),
 ("2026 UX 트렌드: 인지 부하 줄이는 '차분한 인터페이스' 부상",
  "2026 UX/UI 트렌드의 핵심은 인지 부하 감소다. 게이미피케이션 대신 차분한 마이크로인터랙션으로, 여백·선명도·직관적 흐름이 AI 시대 디지털 제품 디자인의 새 기준으로 자리 잡고 있다.",
  "Envato Elements", "https://elements.envato.com/learn/ux-ui-design-trends"),
 ("피그마, 캔버스 내 AI 에이전트로 '아이디어→제품' 속도 가속",
  "피그마가 디자인 캔버스에 직접 AI 에이전트를 내장한 4가지 신규 AI 도구를 공개했다. 디자인 시스템 준수를 유지하며 생성·리믹스·반복 작업을 자동화해 아이디어에서 실제 제품까지의 속도를 획기적으로 높인다.",
  "Figma Blog", "https://www.figma.com/blog/4-new-ways-to-go-from-idea-to-product-with-ai-tools/"),
]

MARKETING = [
 ("칸 라이언즈 2026 Day 2 그랑프리 — Adidas·Apple TV·Coinbase 수상",
  "6월 24일 칸 라이언즈 엔터테인먼트·크래프트 부문 그랑프리가 발표됐다. Adidas '오리지널 포에버'가 더블 수상, Coinbase 비디오게임 영상, De'Longhi 미니어처 커피숍이 크래프트 부문을 석권했다.",
  "Ad Age", "https://adage.com/events-awards/cannes-lions/aa-grand-prix-2026-tuesday-winners/"),
 ("칸 라이언즈 2026 Day 1 그랑프리 — Heinz·Mercado Livre·ABI InBev",
  "6월 23일 첫 그랑프리가 발표됐다. Heinz '여기 어디서 봤죠?' 프린트 캠페인, Mercado Livre의 104m 스타디움 바코드, ABI InBev 'Creativity at Scale'이 각 부문을 석권했다.",
  "Cannes Lions", "https://www.canneslions.com/news/73rd-cannes-lions-international-festival-of-creativity-announces-first-winners"),
 ("인스타그램, 'AI 크리에이터' 라벨 도입 — 파트너십 구조 변화",
  "인스타그램이 AI로 콘텐츠를 정기적으로 제작하는 계정에 'AI 크리에이터' 라벨을 출시했다. AI 도구 브랜드들이 라벨 계정과 40~60만 달러 규모 장기 파트너십을 체결하는 새 시장이 형성되고 있다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/instagram-adds-ai-creator-labels/819267/"),
 ("TikTok World 2026 광고 업데이트 — AI 창작·커머스 풀퍼널화 선언",
  "TikTok World 2026에서 AI 보조 창작·구매 연동·검색 허브 등 광범위한 광고 업데이트가 발표됐다. 틱톡이 숏폼 플랫폼을 넘어 완전한 풀퍼널 광고 생태계로의 전환을 공식 선언했다.",
  "ALM Corp", "https://almcorp.com/blog/tiktok-world-2026-ad-tool-announcements/"),
 ("구글 AI Overviews 업데이트 — 검색 트래픽을 웹사이트로 되돌린다",
  "구글이 AI Overviews와 AI 모드를 업데이트해 2년간 잠식했던 사이트 트래픽을 다시 원점으로 되돌리겠다고 발표했다. 크리에이터·브랜드를 위한 검색 프로필 기능도 단계적으로 도입 중이다.",
  "Seafoam Media", "https://seafoammedia.com/june-2026-marketing-news-trends-insights/"),
 ("Adweek 50 발표 — 역경을 기회로 바꾼 마케터 50인 선정",
  "Adweek이 2026년 가장 영향력 있는 마케터 50인을 선정했다. AI·데이터·창의성을 결합해 업계 변화를 이끄는 CMO와 브랜드 리더들로, 한 해 가장 치열한 도전을 성장 기회로 바꾼 인물들이 주목받았다.",
  "Adweek", "https://www.adweek.com/brand-marketing/the-2026-adweek-50-are-up-for-the-challenge/"),
 ("CommerceNext 2026 핵심 화두 — 리테일 미디어·AI 쇼핑·CAC",
  "6월 23~24일 뉴욕 CommerceNext Growth Show 2026의 핵심 주제는 리테일 미디어·AI 쇼핑 경험·고객 확보 비용이었다. 아마존·틱톡샵·인스타그램 쇼핑에서의 브랜드 전략이 집중 논의됐다.",
  "Two Octobers", "https://twooctobers.com/blog/digital-marketing-updates-june-2026/"),
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
