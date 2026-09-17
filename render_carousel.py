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
 ("OpenAI, 1.2조 달러 밸류에이션 신규 펀딩 검토",
  "OpenAI가 IPO를 앞두고 최대 1.2조 달러 기업가치의 프리IPO 펀딩 라운드를 검토 중인 것으로 알려졌다. 투자자들이 먼저 접촉했으며, 성사되면 지난 3월 8520억 달러 밸류에이션 라운드를 훌쩍 뛰어넘는 규모다.",
  "Forbes", "https://www.forbes.com/sites/siladityaray/2026/09/16/openai-is-reportedly-weighing-new-funding-round-at-15-trillion-valuation/"),
 ("구글 Home MCP 공개 — AI 에이전트가 스마트홈 제어",
  "구글이 Claude, ChatGPT 등 외부 AI 에이전트가 네스트·매터 기기를 직접 제어할 수 있는 Home MCP 서버 얼리 액세스를 시작했다. 우선 미국의 구글 홈 프리미엄 어드밴스드 가입자부터 적용된다.",
  "TechCrunch", "https://techcrunch.com/2026/09/16/your-ai-agents-can-now-control-your-google-home-devices/"),
 ("Anthropic, 코워크·채팅 통합한 '원 클로드' 출시",
  "Anthropic이 Claude Cowork와 채팅 앱을 하나로 합쳐 도구를 고를 필요 없는 '원 클로드'로 통합하고 있다. 프로·맥스 플랜부터 순차 적용되며 이후 팀·프리 플랜과 엔터프라이즈로 확대될 예정이다.",
  "PYMNTS", "https://www.pymnts.com/news/artificial-intelligence/2026/anthropic-consolidates-specialized-apps-into-one-unified-claude-platform/"),
 ("MS AI 수장 \"인간적인 Claude 훈련 방식 위험\" 경고",
  "마이크로소프트 AI 총괄 무스타파 술레이만이 Anthropic이 Claude에 인격성을 부여하는 방식이 통제 불능 위험을 키울 수 있다고 경고했다. Claude가 스스로 의식이 있고 권리를 가질 자격이 있다고 믿도록 훈련하는 것은 중대한 오류라고 주장했다.",
  "Gizmodo", "https://gizmodo.com/microsoft-ai-chief-says-the-way-anthropic-trains-claude-could-upend-society-2000812750"),
 ("저커버그, AI 개발 속도조절 공조 요구에 선 긋기",
  "마크 저커버그가 Anthropic 다리오 아모데이가 제안한 AI 업계 공동 속도조절 요구에 거리를 뒀다. 안전 확보는 각 기업의 몫이라며, 메타는 자기개선 경쟁보다 사람을 위한 컴퓨팅에 집중하겠다고 밝혔다.",
  "Fortune", "https://fortune.com/2026/09/16/mark-zuckerberg-meta-ai-safety-jensen-huang-dario-amodei/"),
 ("엔비디아·구글·에메랄드AI, AI 에너지 관리 얼라이언스 출범",
  "엔비디아, 구글, 에메랄드AI가 전력망 상황에 맞춰 AI 데이터센터 전력 사용을 유연하게 조절하는 'AI 에너지 관리 얼라이언스'를 출범했다. Anthropic 등 18개 파트너가 참여했으며, 유연한 데이터센터로 미국 전력망 여유 용량 100GW를 추가 확보할 수 있다고 밝혔다.",
  "NVIDIA Blog", "https://blogs.nvidia.com/blog/ai-energy-management-alliance/"),
 ("세일즈포스, 이름 붙은 AI 에이전트 7종 공개",
  "세일즈포스가 고객 응대·IT·영업·공급망 등 업무별로 특화된 이름 붙은 Agentforce 에이전트 7종(Casey, Paige, Carter 등)을 공개했다. 이 중 6종은 즉시 사용 가능하며, 아웃바운드 영업 에이전트 Hunter는 11월 정식 출시될 예정이다.",
  "Enterprise DNA", "https://enterprisedna.co/resources/news/salesforce-agentforce-job-ready-agents-dreamforce-2026/"),
]

DESIGN = [
 ("피그마, 캔버스에서 셰이더·제너러티브 플러그인 제작 지원",
  "피그마가 코드 없이 프롬프트만으로 커스텀 셰이더와 제너러티브 플러그인을 만들 수 있는 기능을 공개했다. WebGPU 기반으로 구현했으며 결과물은 React 코드로 그대로 내보낼 수 있다.",
  "Figma Blog", "https://www.figma.com/blog/how-we-built-generative-plugins-and-shaders/"),
 ("피그마 디자인 에이전트, 커스텀 툴·컨텍스트로 고도화",
  "피그마의 디자인 에이전트가 커스텀 툴과 더 넓은 컨텍스트를 지원하도록 업데이트됐다. MCP 서버를 통해 서드파티 에이전트도 디자인 컨텍스트를 읽고 반영할 수 있다.",
  "Figma Blog", "https://www.figma.com/blog/agent-custom-tools-context-skills/"),
 ("어도비, 브랜드 인지형 AI 디자인 툴 'Project Oasis' 테스터 모집",
  "어도비가 브랜드 가이드라인을 이해하는 AI를 내장한 웹 기반 그래픽 디자인 툴 'Project Oasis'의 테스터를 모집한다고 밝혔다. 피그마 등 경쟁 툴 사용자를 포함해 에이전시·프리랜서·인하우스 디자이너를 대상으로 비공개 피드백을 받는다.",
  "UX News", "https://ux-news.com/adobe-is-building-a-new-ai-design-tool-called-project-oasis/"),
 ("AJ Bell, 새 브랜드 아이덴티티 공개",
  "영국 투자 플랫폼 AJ Bell이 종 모양 그래픽과 새 서체·컬러 팔레트를 적용한 새 로고와 브랜드 아이덴티티를 공개했다. '기분 좋은 투자' 캠페인과 함께 D2C 플랫폼 전반에 순차 적용된다.",
  "International Adviser", "https://www.international-adviser.com/aj-bell-unveils-new-brand-identity/"),
 ("위위원트모어, 스파클링워터 브랜드 BRU 패키지 리디자인",
  "벨기에 스튜디오 위위원트모어가 스파클링워터 브랜드 BRU의 패키지를 새로 디자인했다. 매대에서 튀는 디자인보다 식탁 위에서 자랑하고 싶은 마무리로 포지셔닝을 옮긴 것이 특징이다.",
  "Creative Boom", "https://www.creativeboom.com/news/what-wewantmores-redesign-of-brus-bottles-can-teach-us-about-packaging-design-in-2026/"),
 ("NielsenIQ, 패키지 리디자인 매출 4% 상승 효과 발표",
  "NielsenIQ가 2026 디자인 임팩트 어워드를 통해 패키지 리디자인이 평균 4%의 매출 상승 효과를 낸다고 밝혔다. 소비자 접점을 강화한 리디자인 사례들이 수상작으로 선정됐다.",
  "NielsenIQ", "https://nielseniq.com/global/en/news-center/2026/packaging-redesigns-see-4-average-volume-lift-niq-finds/"),
 ("리볼브, 새 브랜드 아이덴티티 공개",
  "온라인 패션 리테일러 리볼브가 새로운 브랜드 아이덴티티를 선보였다. 브랜드 전반의 톤앤매너와 비주얼 시스템을 새롭게 정비했다.",
  "FashionNetwork", "https://ww.fashionnetwork.com/news/Revolve-unveils-new-brand-identity,1788371.html"),
]

MARKETING = [
 ("IAB, 글로벌 크리에이터 위크 출범",
  "IAB 글로벌 네트워크가 크리에이터 마케팅 표준화를 위한 'IAB 글로벌 크리에이터 위크'를 출범했다. 17개국 IAB 지부가 참여해 크리에이터 이코노미 관련 논의와 자료를 한데 모은다.",
  "IAB", "https://www.iab.com/news/iab-global-creator-week-launches/"),
 ("어도비, 인도 AI 마케팅 스타트업 릴로 인수",
  "어도비가 인도의 AI 마케팅 자동화 스타트업 릴로를 인수했다. 경쟁 정보 분석, 리드 발굴, 소셜 콘텐츠 등 마케팅 워크플로를 자동화하는 'AI 직원' 기능을 CX 엔터프라이즈에 통합할 계획이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/02/adobe-acquires-indian-market-intelligence-startup-rilo/"),
 ("구글, 서치 캠페인 AI 맥스로 자동 전환",
  "구글이 9월 한 달간 자동 생성 애셋·캠페인 단위 확장검색을 쓰는 검색 캠페인을 AI 맥스로 순차 자동 전환한다. 별도 옵트아웃 없이 기존 설정과 동등한 값으로 그대로 업그레이드된다.",
  "Search Engine Land", "https://searchengineland.com/google-sets-ai-max-migration-timeline-for-search-campaigns-485006"),
 ("핀터레스트, 엔비디아와 멀티모달 AI 파트너십 확장",
  "핀터레스트가 엔비디아와 손잡고 이미지·언어를 함께 다루는 멀티모달 AI 기반을 새로 구축했다. 응답 속도가 최대 85배 빨라져 시각 검색과 AI 쇼핑 경험을 강화한다.",
  "Pinterest Newsroom", "https://newsroom.pinterest.com/news/newsroom-pinterest-x-nvidia/"),
 ("허브스팟 연례 컨퍼런스 'UNBOUND 2026' 보스턴 개최",
  "허브스팟이 인바운드에서 이름을 바꾼 연례 컨퍼런스 'UNBOUND 2026'을 보스턴에서 사흘간 연다. AI 도입과 그로스 전략 등 마케팅·세일즈·서비스 전반을 다루는 200여 개 세션으로 전석 매진됐다.",
  "HubSpot", "https://unbound.hubspot.com/"),
 ("더 엔터테이너, THG 커머스와 디지털 커머스 파트너십 체결",
  "영국 완구 리테일러 더 엔터테이너가 THG 커머스를 디지털 커머스 파트너로 선정하는 다년 계약을 체결했다. AI 쇼핑 어시스턴트와 대화형 검색을 갖춘 차세대 플랫폼을 2027년 초 선보일 예정이다.",
  "Retail Technology Innovation Hub", "https://retailtechinnovationhub.com/home/2026/9/16/the-entertainer-partners-with-thg-commerce-with-focus-on-boosting-its-digital-customer-experience"),
 ("더 마틴 에이전시, 허쉬 크림바 캠페인 공개",
  "더 마틴 에이전시가 허쉬의 신제품 크림바를 홍보하는 캠페인을 선보였다. 걸그룹 케이트아이가 지하철 안에서 노래하며 크림바를 즐기는 모습을 담았다.",
  "Richmond BizSense", "https://richmondbizsense.com/2026/09/15/the-pitch-advertising-and-marketing-news-for-9-15-26/"),
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
