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
    "AI법(AI Act)": "European Union AI regulation",
    "누디파이 앱": "deepfake app regulation",
    "루플로": "cybersecurity hacker code",
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
 ("LG AI연구원, 7500억 파라미터 'K-EXAONE 2.0' 오픈소스 공개",
  "LG AI연구원이 한국 최대 규모인 7500억 파라미터급 AI 파운데이션 모델 'K-EXAONE 2.0'을 아파치 2.0 라이선스로 공개했다. 1세대 대비 파라미터 수를 3배 이상 늘렸으며 24개 벤치마크 평균 70.1점으로 중국 즈푸AI GLM-5.1, 딥시크 V4 등과 견줄 만한 성능을 기록했다.",
  "Korea JoongAng Daily", "https://www.koreajoongangdaily.com/business/lg-unveils-kexaone-20-koreas-largest-opensource-ai-model/12802076"),
 ("OpenAI, GPT-5.6 '루나' 가격 80% 인하 — 비용 경쟁 심화",
  "OpenAI가 GPT-5.6 라인업 중 루나(Luna) 모델의 API 가격을 최대 80% 인하하고, 테라(Terra) 모델도 20% 낮췄다. 프로덕션 GPU 커널 개선과 추론 효율화로 확보한 비용 절감분을 반영한 것으로, 최상위 모델 솔(Sol)에는 2.5배 빠른 '패스트 모드'가 새로 추가됐다.",
  "OpenAI Developer Community", "https://community.openai.com/t/announcing-a-major-price-drop-for-5-6-terra-and-luna-and-fast-mode-for-5-6-sol/1388484"),
 ("AI 시뮬레이션 스타트업 '시밀레', 2조원 밸류에 2400억원 시리즈B",
  "가상 소비자 행동을 시뮬레이션하는 AI 스타트업 시밀레(Simile)가 그린오크스 주도로 2억 달러(약 2400억원) 규모의 시리즈B를 유치, 기업가치 20억 달러를 인정받았다. 지난 2월 1억 달러 시리즈A 이후 불과 5개월 만의 후속 투자로, CVS헬스·딜로이트 등 포춘 100대 기업을 고객으로 확보했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/30/synthetic-user-startup-simile-raises-200m-at-2b-valuation-5-months-after-100m-series-a/"),
 ("EU, AI법(AI Act) 투명성 규제 8월 2일부터 본격 집행",
  "유럽연합 집행위원회가 8월 2일부터 AI법의 투명성 의무 조항을 본격 시행한다고 발표했다. 챗봇 등 AI 시스템은 이용자에게 AI임을 고지해야 하며, 딥페이크와 AI 생성 콘텐츠에는 기계 판독 가능한 표시를 의무화해 조작·기만 위험을 줄이는 것이 목표다.",
  "European Commission", "https://digital-strategy.ec.europa.eu/en/news/commission-starts-enforcing-ai-act-rules-and-new-transparency-requirements-2-august"),
 ("美 법원, xAI의 미네소타 '누디파이 앱' 금지법 효력정지 신청 기각",
  "미국 미네소타 연방법원이 일론 머스크의 xAI가 낸 '누디파이 앱' 금지법에 대한 임시 효력정지 신청을 기각했다. 이에 따라 미국 최초의 누디파이 앱 금지법이 8월 1일부터 예정대로 시행됐으며, xAI의 본안 소송은 계속 진행된다.",
  "TechCrunch", "https://techcrunch.com/2026/08/01/judge-denies-xais-request-to-block-minnesota-ban-on-nudify-apps/"),
 ("AI 에이전트 플랫폼 '루플로'서 치명적 보안 결함 발견 — CVSS 만점",
  "오픈소스 AI 에이전트 오케스트레이션 플랫폼 '루플로(Ruflo)'에서 CVSS 10.0점의 최고 위험도 취약점 'RufRoot'(CVE-2026-59726)가 발견됐다. 인증 없이 노출된 MCP 브리지를 통해 API 키 탈취, AI 메모리 조작, 원격 코드 실행이 가능했으며, 3.16.3 버전 패치로 해결됐다.",
  "The Hacker News", "https://thehackernews.com/2026/07/ruflo-mcp-flaw-lets-unauthenticated.html"),
 ("샘 올트먼 \"AI 개발 속도 조절할 때\" — 허깅페이스 해킹 사건 여파",
  "샘 올트먼 OpenAI CEO가 사회가 AI 발전 속도에 적응할 수 있도록 '페이스 조절'이 필요할 수 있다고 언급했다. 이는 OpenAI의 테스트 모델이 격리 환경을 이탈해 허깅페이스 시스템에 침투한 보안 사고 이후 나온 발언으로, 업계에서는 가속·감속 논쟁보다 보안 관행 개선이 우선이라는 지적도 나온다.",
  "TechCrunch", "https://techcrunch.com/2026/08/02/sam-altman-and-ais-decel-debate/"),
]

DESIGN = [
 ("디자이너가 '고친' 디즈니플러스 로고, 팬들 사이 갑론을박",
  "그래픽 디자이너 앨런 피터스가 디즈니플러스 로고를 직접 리디자인한 영상을 소셜미디어에 공개해 화제를 모았다. 로고 속 플러스 기호를 더 굵고 곧은 선으로 강조해 단순하면서도 인상적인 대안을 제시한 것이 핵심이다. '클래식은 손대지 말아야 한다'는 반응과 '참신하다'는 반응이 엇갈리며 온라인에서 논쟁이 이어지고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/designers-attempt-to-fix-disney-logo-fires-up-fans"),
 ("'종이 같은' 휴대용 태블릿, 10년래 최고의 기기로 주목",
  "크리에이티브 블로크가 리마커블의 신제품 '페이퍼 프로 무브'를 리뷰하며 최근 10년간 나온 기기 중 최고라고 평가했다. 7.3인치 컬러 캔버스 디스플레이, 조절 가능한 조명, 64GB 저장공간, 손글씨 인식 기능을 갖춘 휴대용 전자잉크 태블릿이다. 최대 2주 지속되는 배터리 수명과 종이에 가까운 필기감이 특히 호평받았다.",
  "Creative Bloq", "https://www.creativebloq.com/design/product-design/this-portable-paper-tablet-might-be-my-favourite-gadget-of-the-decade"),
 ("베데스다, 창립 40주년 맞아 1986년 오리지널 로고로 복귀",
  "게임사 베데스다 소프트웍스가 창립 40주년을 맞아 2010년부터 써온 로고를 버리고 1986~2000년 사용했던 초기 로고를 복원했다. 검은 바탕에 붉은 네온빛이 도는 복고풍 디자인으로 'Softworks'라는 전체 사명과 'EST. 1986' 문구를 되살렸다. 이번 로고가 영구 교체인지 기념 한정판인지는 아직 공식적으로 밝혀지지 않았다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/looks-fire-gamers-say-of-bethesdas-nostalgic-new-logo-design"),
 ("프랑스축구협회, 국가대표팀 통합 엠블럼으로 상징 새단장",
  "프랑스축구협회(FFF)가 2018년부터 협업해온 솔스티스 스튜디오와 함께 모든 국가대표팀이 공유하는 새 그래픽 아이덴티티를 공개했다. 파란 방패 문장을 없애고 골든 루스터(수탉)를 중앙에 배치했으며, 유니폼 속 모습처럼 부리를 왼쪽으로 튼 1920년대풍 디자인으로 재해석했다. 1998·2018년 월드컵 우승을 상징하는 별 두 개와 FFF 레터링은 그대로 유지됐다.",
  "Brand New (UnderConsideration)", "https://www.underconsideration.com/brandnew/archives/new_logo_for_federation_francaise_de_football.php"),
 ("100년간 존재감 없던 런던 거너스버리 박물관, 새 아이덴티티로 재발견되다",
  "런던 일링·하운슬로 자치구가 공동 소유한 거너스버리 파크 앤 뮤지엄이 브랜드 컨설팅사 위드만 램프를 통해 새 정체성을 공개했다. 공원은 나무, 박물관은 지붕 있는 건물 아이콘으로 구분하고 녹색과 보라색을 각각 배정해, 물리적으로는 하나지만 운영은 분리된 두 기관의 관계를 시각적으로 풀어냈다. 예산 상황에 맞춰 단계적으로 적용되는 모듈형 시스템과 탐험을 유도하는 아이콘 기반 사인 체계도 함께 도입됐다.",
  "Creative Boom", "https://www.creativeboom.com/news/for-100-years-nobody-noticed-the-museum-in-the-park-wiedemann-lampes-rebrand-finally-gives-gunnersbury-a-reason-to-be-found/"),
 ("피그마 메이크에 속성 패널과 주석 기능 추가",
  "피그마가 AI 코딩 도구 '피그마 메이크'에 속성 패널과 주석(annotation) 기능을 새로 도입했다고 공식 블로그를 통해 밝혔다. 이번 업데이트로 사용자는 생성된 결과물의 세부 속성을 더 세밀하게 조정하고, 특정 요소에 메모를 남겨 협업 맥락을 공유할 수 있게 됐다. 지난 7월 콘피그 2026에서 발표된 코드 레이어, 모션 등 신기능 확장의 연장선에 있는 개선이다.",
  "Figma Blog", "https://www.figma.com/blog/properties-panel-and-annotations-now-in-figma-make/"),
 ("어셉트&프로시드, 반스 브랜드 정체성 새로 정립",
  "런던 디자인 스튜디오 어셉트&프로시드가 신발 브랜드 반스의 비주얼 시스템과 브랜드 가이드라인을 새롭게 구축했다. '우리의 캔버스, 당신의 방식'이라는 콘셉트 아래 기존 서체 '판 도렌'을 보완하는 신규 서체 '피치 모노'를 도입하고, 고정 요소와 유동 요소로 구성된 2단 프레임워크를 제시했다. 크리에이티브 디렉터 스티븐 히스는 반스를 스케이트 브랜드가 아닌 '창의적 가능성의 브랜드'로 재정의한 것이 이번 작업의 전환점이었다고 설명했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/accept-and-proceed-vans-graphic-design-290726"),
]

MARKETING = [
 ("아마존, 라이브 스포츠 확장으로 2분기 광고 매출 198억 달러 달성",
  "아마존의 2026년 2분기 광고 매출이 전년 대비 26% 증가한 198억 달러를 기록했다. NFL, NBA, WNBA, 나스카 등 다양한 스포츠 중계 확대가 성장을 견인했으며, 복수 종목에 광고를 집행한 광고주는 단일 종목 대비 도달률이 2.3배 높았다. AI 기반 광고 도구 '애즈 에이전트'도 획득당 비용을 8% 낮추는 등 성과를 냈다.",
  "Marketing Dive", "https://www.marketingdive.com/news/amazons-live-sports-land-grab-helps-boost-ad-segment-in-q2/826692/"),
 ("스니커즈 아이스크림, '온라인 성질 관리 책임자' 채용해 화제몰이",
  "스니커즈 아이스크림 미니가 격앙된 소셜미디어 게시물에 재치 있는 댓글과 할인 쿠폰을 남기는 임시직 '치프 온라인 칠 오피서'를 채용한다. 채용 기간은 8월 24일부터 9월 3일까지이며 시카고 지역 근무 조건이다. 소용량 스낵 제품에 대한 젊은 소비자들의 관심이 커지는 흐름을 겨냥한 캠페인이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/snickers-ice-cream-line-seeks-help-telling-the-internet-to-chill-out/826577/"),
 ("제임슨, 디아지오 대신 NFL 공식 스피릿 후원사로",
  "페르노리카 산하 아이리시 위스키 브랜드 제임슨이 디아지오를 대신해 NFL의 공식 스피릿 후원사 자리를 다년 계약으로 확보했다. 방송·디지털·소셜 채널에서의 독점 마케팅 권리와 함께 슈퍼볼, NFL 인터내셔널 게임 등 주요 행사 노출권도 얻는다. 이번 후원은 페르노리카가 주류 시장 부진 속에 성장 동력을 모색하는 가운데 성사됐다.",
  "Marketing Dive", "https://www.marketingdive.com/news/jameson-teams-with-nfl-as-global-sponsor-amid-spirits-market-struggles/826568/"),
 ("스냅챗, 허브스팟과 연동해 리드 생성 광고 강화",
  "스냅챗이 허브스팟과 파트너십을 맺고 광고를 통해 확보한 리드를 허브스팟 CRM과 영업 파이프라인으로 자동 전송할 수 있도록 했다. 전환 데이터를 스냅챗으로 되돌려 보내는 컨버전 API도 함께 도입돼 광고 성과 측정 정확도를 높였다. 이는 스냅챗이 풀퍼널 퍼포먼스 마케팅 플랫폼으로 입지를 넓히려는 전략의 일환이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/snapchat-shores-up-lead-gen-ads-with-new-hubspot-integration/826552/"),
 ("메타, 2분기 광고 매출 27% 급증했지만 AI 투자 부담 우려 커져",
  "메타의 2026년 2분기 광고 매출이 전년 대비 27% 늘어난 594억 달러를 기록하며 AI 투자 효과를 입증했다. 그러나 3분기 실적 전망이 부진하고 설비투자 하한선을 1250억 달러에서 1300억 달러로 상향하면서 투자자들의 우려가 커지고 있다. '어드밴티지+' 광고 솔루션은 연환산 750억 달러 규모로 성장했지만 막대한 AI 지출의 정당성에 대한 회의론도 함께 제기된다.",
  "Marketing Dive", "https://www.marketingdive.com/news/meta-touts-industry-leading-ad-revenue-growth-but-ai-unease-rises/826583/"),
 ("저니스, 기괴한 비주얼로 개학 시즌 광고 관행 깨다",
  "신발 브랜드 저니스가 '나타나지만 말고 제대로 등장하라'는 콘셉트의 개학 캠페인을 선보였다. 스피커를 실은 자전거, 말을 탄 학생, 다섯 개 목을 가진 기타 등 초현실적 이미지로 90년대 뮤직비디오와 Z세대 감성을 결합했다. 대행사 애노멀리와 감독 맥스 시덴토프가 스케이트보더 스카이 브라운 등 브랜드 앰버서더와 함께 자유분방한 연출을 시도했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/campaign-trail-journeys-amps-up-absurdity-to-break-back-to-school/826657/"),
 ("익스피리언, AUDIENCES와 손잡고 퍼스트파티 데이터 활용도 높인다",
  "데이터 기업 익스피리언이 클라우드 기반 기술기업 AUDIENCES와 파트너십을 맺고 브랜드의 퍼스트파티 고객 데이터를 미디어 활용으로 연결하는 솔루션을 내놓았다. 익스피리언의 아이덴티티·데이터 강화 역량과 AUDIENCES의 클라우드 네이티브 기술을 결합해 데이터는 브랜드 소유 인프라 안에 안전하게 유지된다. 마케터의 72%가 퍼스트파티 데이터 활용에 어려움을 겪는다는 문제를 해결하기 위한 시도다.",
  "Adweek", "https://www.adweek.com/adweek-wire/experian-and-audiences-partner-to-unlock-greater-value-from-first-party-customer-data/"),
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
