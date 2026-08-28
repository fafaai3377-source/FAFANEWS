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
 ("앤트로픽, 엔스케일과 450억 달러 컴퓨팅 계약",
  "앤트로픽이 영국 AI 인프라 기업 엔스케일과 6년간 약 450억 달러 규모의 클라우드 컴퓨팅 계약을 맺었다. 웨스트버지니아 데이터센터에서 엔비디아 베라 루빈 칩 기반 460메가와트 용량을 확보하며 상장(IPO)을 앞두고 컴퓨팅 자원 확보에 나섰다.",
  "TechCrunch", "https://techcrunch.com/2026/08/26/anthropic-continues-compute-gobbling-streak-in-45-billion-deal-with-nscale/"),
 ("Z.ai, 오픈소스 멀티모달 모델 'GLM-5.3-Flash' 공개",
  "중국 Z.ai가 3200억 파라미터 중 180억만 활성화하는 MoE 구조의 GLM-5.3-Flash를 MIT 라이선스로 공개했다. 텍스트·이미지·영상을 지원하는 첫 네이티브 멀티모달 GLM-5 모델로 100만 토큰 컨텍스트를 지원한다.",
  "Hugging Face", "https://huggingface.co/zai-org/GLM-5.3-Flash"),
 ("구글, Gemini Omni 1.1 Flash 영상 편집 기능 확대",
  "구글이 Gemini Omni 1.1 Flash의 영상 생성·편집 기능을 확장했다. 장면을 40초까지 늘리고 4K로 업스케일하며 구도를 제어할 수 있어 AI 스튜디오에서 저렴한 비용으로 영상 초안을 완성할 수 있다.",
  "Google Blog", "https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-omni-1-1-flash/"),
 ("美 항소법원, AI 가격 알고리즘 반독점 소송 되살려",
  "제3순회항소법원이 애틀랜틱시티 카지노들이 사용한 AI 가격결정 플랫폼 '레인메이커'에 대한 반독점 소송을 부활시켰다. AI 기반 가격 담합 우려가 다시 법적 쟁점으로 떠올랐다.",
  "Gambling News", "https://www.gamblingnews.com/news/us-appeals-court-revives-ai-casino-pricing-lawsuit/"),
 ("허쉬, AI로 '스모어 시즌' 매대 배치 최적화",
  "허쉬가 컴퓨터 비전 AI로 매장별 스모어 재료의 최적 진열 위치를 분석했다. AI가 제안한 배치를 적용한 매장은 판매량이 최대 101% 늘며 매출 효과를 입증했다.",
  "Modern Retail", "https://www.modernretail.co/marketing/hershey-starts-using-ai-to-strategize-for-smores-season/"),
 ("NIQ \"쇼핑객 74%, 제품 탐색에 AI 활용\"",
  "NIQ와 월드데이터랩의 새 보고서에 따르면 전 세계 쇼핑객의 74%가 제품 탐색 과정에서 AI를 활용하는 것으로 나타났다. AI가 구매 여정 전반에 깊숙이 자리잡으며 브랜드의 대응 전략 마련이 시급해졌다.",
  "NielsenIQ", "https://nielseniq.com/global/en/news-center/2026/74-of-shoppers-use-ai-for-discovery-niq-showcases-what-that-means-for-the-consumer-purchase-journey-in-new-report/"),
 ("깃허브 \"오픈소스 국경간 협업, 분기 최대폭 증가\"",
  "깃허브의 2026년 1분기 이노베이션 그래프에 따르면 국가 간 오픈소스 협업이 전분기 대비 16% 늘며 2020년 이후 두 번째로 높은 증가율을 기록했다. 에이전트 프레임워크·MCP 서버 등 AI 애플리케이션 개발이 성장을 이끌고 있다.",
  "GitHub Blog", "https://github.blog/news-insights/policy-news-and-insights/q1-2026-innovation-graph-update-open-source-collaboration-is-accelerating-worldwide/"),
]

DESIGN = [
 ("인스타그램, 10년 만에 첫 워드마크 리프레시",
  "인스타그램이 2016년 이후 유지해온 필기체 워드마크를 10년 만에 개편했다. 더 굵고 단순해진 형태와 함께 손글씨체 'Pen', 모노스페이스 'Mono' 등 새 서체 세 종을 도입했다.",
  "tbreak", "https://tbreak.com/new-instagram-logo-wordmark-refresh/"),
 ("Envato, 18주년 맞아 새 로고·소닉 아이덴티티 공개",
  "디지털 자산 마켓플레이스 Envato가 18주년을 맞아 새 워드마크와 사운드 브랜딩을 공개했다. 시그니처 그린을 더 밝고 대담하게 재해석하고 뉴욕 작곡가와 협업한 전용 사운드를 도입했다.",
  "Creative Boom", "https://www.creativeboom.com/news/envato-launches-new-brand-identity/"),
 ("Koto, 스택 오버플로우를 'AI 시대'에 맞게 리브랜드",
  "크리에이티브 스튜디오 Koto가 개발자 커뮤니티 스택 오버플로우의 브랜드를 전면 개편했다. '항상 빌드 중'이라는 콘셉트로 AI가 대체할 수 없는 커뮤니티의 가치를 강조하는 정체성을 구축했다.",
  "Creative Boom", "https://www.creativeboom.com/news/koto-reframes-stack-overflow-around-the-one-thing-ai-cant-replace-its-community/"),
 ("TBWA, 새 글로벌 비주얼 아이덴티티 공개",
  "TBWA가 사내 디자인 조직 DXD와 함께 손맛이 느껴지는 새 글로벌 아이덴티티를 선보였다. 잉크 자국·손글씨 등 아날로그적 요소로 AI 시대에 반하는 '인간적 손길'을 강조했다.",
  "Campaign Brief Asia", "https://campaignbriefasia.com/2026/08/05/dxd-brings-tbwas-disruption-philosophy-to-life-with-new-global-identity/"),
 ("코카콜라, 새로운 글로벌 비주얼 아이덴티티 공개",
  "코카콜라가 200여 개국에 적용할 새로운 글로벌 비주얼 시스템을 공개했다. 브랜드의 상징성을 유지하면서도 더 역동적이고 확장성 있는 룩으로 일관성을 강화했다.",
  "The Coca-Cola Company", "https://www.coca-colacompany.com/media-center/coca-cola-sharpens-its-identity-under-one-bold-visual-system"),
 ("4Creative, '컷아웃' 비주얼로 새 아이덴티티 공개",
  "채널4의 크리에이티브 조직 4Creative가 '그레이트 브리티시 베이크오프' 등 자사 프로그램 이미지를 오려붙인 듯한 새 로고를 선보였다. 팀원이 계속 더해가는 '영원히 미완성'인 구조로 설계됐다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/4creative-rebrand-graphic-design-project-070526"),
 ("피그마 데스크톱 앱, 에이전트 채팅 패널 분리 창 지원",
  "피그마가 데스크톱 앱(macOS·Windows)에서 에이전트 채팅 패널을 별도 창으로 분리할 수 있도록 업데이트했다. 다른 작업으로 탭을 전환해도 패널이 계속 보여 멀티태스킹이 쉬워졌다.",
  "Figma Release Notes", "https://www.figma.com/release-notes/"),
]

MARKETING = [
 ("OpenAI, 인도서 ChatGPT 광고 출시 — 50개 이상 브랜드 참여",
  "OpenAI가 인도에서 무료·Go 등급 이용자를 대상으로 ChatGPT 광고를 시작했다. WPP·옴니콤을 첫 에이전시 파트너로 삼았으며 50개 이상 브랜드가 이번 주 광고를 게재한다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/chatgpt-ads-arrive-in-india-but-brands-may-not-be-ai-ready-yet-mandeep-malhotra-12442420"),
 ("X, '리드 젠 광고' 전 세계 출시",
  "X가 플랫폼 이탈 없이 네이티브 폼으로 리드를 수집하는 '리드 젠 광고'를 전 세계 광고주 대상으로 출시했다. 이미지·영상·캐러셀 등 다양한 포맷을 지원하며 CRM 연동도 가능하다.",
  "Campaign", "https://www.campaignlive.co.uk/article/x-announces-global-launch-lead-gen-ads/1968475"),
 ("메타, 180억 달러 청소년 안전 합의 — 타겟광고는 유지",
  "메타가 미국 청소년 이용자에 하루 2시간 제한과 자정~오전 6시 접속 차단을 도입하는 데 합의했다. 다만 개인 맞춤형 추천과 타겟 광고는 계속 운영하기로 해 광고 사업 영향은 제한적이다.",
  "Anchorage Daily News", "https://www.adn.com/nation-world/2026/08/27/time-limits-and-a-midnight-cutoff-metas-new-rules-for-kids-go-further-but-critics-say-not-enough/"),
 ("컬럼비아대·타불라 연구 \"AI 생성 광고, 클릭률 더 높아\"",
  "컬럼비아대와 타불라가 30만 건 이상의 디스플레이 광고를 분석한 결과 AI 생성 광고의 평균 클릭률이 사람이 만든 광고보다 높았다. 특히 AI 티가 나지 않을수록 성과가 더 좋았다.",
  "Mi3", "https://www.mi-3.com.au/30-01-2026/ai-generated-ads-outperform-human-ads-click-through-rates-study-finds"),
 ("\"美 CMO 99% AI 도입\" — 그러나 생성형 AI 활용은 하락",
  "Censuswide의 '2026 미국 CMO의 목소리' 보고서에서 CMO의 99%가 AI를 어떤 형태로든 도입했다고 답했다. 다만 광고 제작·영상·이메일 자동화 등 실제 생성형 AI 활용률은 오히려 소폭 하락했다.",
  "einpresswire", "https://www.einpresswire.com/article/933351232/ai-adoption-among-us-marketers-reaches-near-universal-levels-but-usage-and-confidence-decline-new-report-finds"),
 ("시트긱, 대화형 AI 검색 'Ask SeatGeek' 출시",
  "티켓 마켓플레이스 시트긱이 자연어로 좌석을 검색하는 'Ask SeatGeek' 기능을 출시했다. 데스크톱·모바일 웹에 순차 적용되며 대화형 검색으로 구매 경험을 단순화한다.",
  "Music Ally", "https://musically.com/2026/08/27/seatgeek-is-the-latest-ticketing-firm-to-add-conversational-ai/"),
 ("PwC \"인도 광고 시장, 연 7.4% 성장\"",
  "PwC 인도 보고서에 따르면 인도 엔터테인먼트·미디어 시장이 연평균 7.4%씩 성장해 2030년 367억 달러 규모에 이를 전망이다. 인터넷 광고가 가장 빠른 성장 부문으로 꼽혔다.",
  "BestMediaInfo", "https://bestmediainfo.com/insights/indias-em-industry-to-grow-74-annually-to-usd-367-bn-by-2030-12444165"),
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
