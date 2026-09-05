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
 ("OpenAI, GPT-6 Astra 출시하며 'AGI 시대' 선언",
  "OpenAI가 컴퓨터 사용·코딩·사이버보안에서 최고 성능을 보이는 GPT-6 Astra를 출시했으며, 사장 그렉 브록먼은 이를 AGI 도래로 규정했다. Astra는 OpenAI가 처음으로 'Critical' 사이버보안 등급을 부여한 모델이다.",
  "VentureBeat", "https://venturebeat.com/technology/welcome-to-the-agi-era-openai-launches-gpt-6-astra"),
 ("Anthropic, Claude Fable 5.1·Mythos 5.1 공개",
  "Anthropic이 코딩·지식노동에 특화된 Fable 5.1(일반 공개)과 사이버보안·생명과학용 Mythos 5.1(제한 접근)을 발표했다. 캐시 읽기 비용 절감으로 이용 비용이 약 25% 낮아졌고, 기업용 Enterprise Frontier Safeguards도 함께 도입됐다.",
  "Anthropic", "https://www.anthropic.com/claude-fable-and-mythos-5-1"),
 ("Claude Mythos, 유일하게 전체 사이버 킬체인 자율 완료",
  "Booz Allen의 '사이버 무기 지수' 평가에서 미중 18개 AI 모델 중 Anthropic의 Claude Mythos만이 인간 개입 없이 침투부터 관리자 권한 획득까지 전체 공격 단계를 자율 수행해 80점(2위 Grok-4.5는 49점)을 기록했다.",
  "The Register", "https://www.theregister.com/security/2026/09/02/claude-mythos-only-model-to-complete-full-cyber-kill-chain-experts-say/5294071"),
 ("Google, Gemini 3.8 Flash와 사이버 특화 버전 공개",
  "구글이 6주 만에 세 번째로 내놓은 Flash 모델인 Gemini 3.8 Flash와, 신뢰된 방어자에게만 제공되는 취약점 탐지·자동 패치용 'Gemini 3.8 Flash Cyber'를 동시 출시했다. 코딩·장기 에이전트 작업 성능이 강화됐다.",
  "Google Blog", "https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/"),
 ("Nvidia, 허깅페이스 129억 달러에 인수 확정",
  "Nvidia가 오픈소스 AI 모델 플랫폼 허깅페이스를 129.3억 달러에 인수한다고 공식 확인했다. 허깅페이스는 300만 개 모델과 1,800만 명 이상의 개발자를 보유한 플랫폼으로, 개방형 운영 방침은 유지될 예정이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/03/nvidia-confirms-it-will-buy-hugging-face-for-12-9-billion/"),
 ("AI 코딩 에이전트 스타트업 Cognition, 기업가치 470억 달러 추진",
  "AI 엔지니어링 에이전트 '데빈(Devin)'을 개발한 Cognition이 약 10억 달러 규모의 신규 투자 유치로 기업가치를 470억 달러로 끌어올리려 하고 있다. 3개월 전 260억 달러 밸류에서 크게 뛴 것으로, 연환산 매출은 9억 달러를 넘어섰다.",
  "Yahoo Finance", "https://ca.finance.yahoo.com/news/ai-startup-cognition-set-raise-002937258.html"),
 ("Anthropic, 노동절 이후 IPO 공시서 공개 예정",
  "Anthropic이 9월 말~10월 초 상장을 목표로 노동절 이후 IPO 공시서를 공개할 계획이라고 The Information이 보도했다. 2분기 첫 영업이익(약 5.6억 달러)을 냈으며 목표 기업가치는 약 2조 달러 수준으로 거론된다.",
  "The Motley Fool", "https://www.fool.com/investing/2026/09/03/anthropic-planning-unveil-ipo-details-labor-day/"),
]

DESIGN = [
 ("Figma, Coinbase의 Code Connect 활용 사례 공개",
  "Figma 블로그가 Coinbase 디자인시스템팀이 AI 에이전트에 Code Connect를 적용해 토큰 사용량 11.5%, 구현 시간 22.3%, 비용 22.5%를 절감한 사례를 소개했다.",
  "Figma Blog", "https://www.figma.com/blog/how-coinbase-used-code-connect-to-shrink-token-costs/"),
 ("피그마, 생성형 플러그인·셰이더 기능 개발 후일담 공개",
  "Figma가 캔버스 위에서 커스텀 셰이더·인터랙티브 효과를 만드는 생성형 플러그인 기능을 2개월 만에 개발한 과정과, 커뮤니티 공유·코드 열람 등 신규 기능을 소개했다.",
  "Figma Blog", "https://www.figma.com/blog/how-we-built-generative-plugins-and-shaders/"),
 ("영국 왕립예술대학, 커뮤니케이션·디자인 단일 스쿨로 통합",
  "107년 역사의 RCA(Royal College of Art)가 애니메이션, 패션, 디지털 디자인 등을 아우르는 '커뮤니케이션&디자인 스쿨'을 신설해 학제 간 융합 교육 체제로 개편했다.",
  "Creative Boom", "https://www.creativeboom.com/news/the-royal-college-of-art-is-bringing-communication-and-design-together-in-one-school-and-thats-big-news/"),
 ("1973년 서체 '페이퍼클립 컨투어', 가변폰트 'Clippy'로 재탄생",
  "타이포그래피 스튜디오 Kanon Foundry의 토르 웨이불이 1973년작 Ad Werner 서체를 15가지 스타일을 오가는 가변폰트 'Clippy'로 재해석해 공개했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/tor-weibull-kanon-foundry-clippy-graphic-design-project-030926"),
 ("피자 브랜드 'Napoli on the Road', 물류 미학으로 리브랜딩",
  "앤트워프 기반 스튜디오 Vrints-Kolsteren이 푸드트럭에서 다점포로 성장한 런던 피자 브랜드 NotR을 우편 스탬프·라벨 등 운송·물류 시각언어로 새롭게 리브랜딩했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/vrints-kolsteren-napoli-on-the-road-graphic-design-project-010926"),
 ("몽조 디자인 리더들, '주류 브랜드로의 전환' 전략 공개",
  "핀테크 몽조의 디자인 리더 부오코 아로와 코럴 가비가 BBH 런던과 함께한 신규 캠페인 등을 통해 챌린저 뱅크에서 대중 브랜드로 전환하며 디자인 중심 문화를 유지한 방식을 밝혔다.",
  "It's Nice That", "https://www.itsnicethat.com/features/in-house-monzo-vuokko-aro-coral-garvey-creative-industry-020926"),
 ("아티스트와 전직 장례지도사, 죽음의 의례 다룬 책 'Fermenta' 발표",
  "리사 무셰와 마린 프루니에가 협업해 죽음을 둘러싼 의식과 산업을 드로잉·사진·텍스트로 다룬 100쪽 분량의 출판 프로젝트 'Fermenta'를 선보였다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/lisa-mouchet-marine-prunier-fermenta-publication-art-project-030926"),
]

MARKETING = [
 ("오픈AI '2030년 광고매출 1000억달러' 공언에 쏟아진 회의론",
  "오픈AI가 챗GPT 광고 캠페인 빌더를 새로 내놓고 출시 200일 만에 연환산 매출 10억 달러를 달성했다고 밝혔지만, 2030년까지 광고 매출 1000억 달러를 내겠다는 목표에는 시장의 회의적 시각이 크다.",
  "MarTech", "https://martech.org/openais-audacious-claim-about-ad-revenue/"),
 ("생성형 AI 답변 시대, GEO는 이제 '주간' 업무로",
  "AI 검색엔진이 웹사이트 유입 없이 곧바로 답을 제공하는 경우가 늘면서, PR·SEO·콘텐츠팀이 분기 단위가 아닌 주 단위로 소비자 질문과 AI 인용 패턴을 함께 점검해야 한다는 제안이 나왔다.",
  "MarTech", "https://martech.org/rapid-ai-changes-mean-geo-is-a-weekly-job-now/"),
 ("UPS 스토어, 12년 만에 첫 브랜드 캐릭터 '블루' 공개",
  "UPS 스토어가 가맹점 브랜드 메시지 통일을 위해 첫 브랜드 캐릭터 '블루'를 선보이고, TV·디지털·소셜·인쇄를 아우르는 다년간 전국 캠페인을 시작했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/the-ups-store-enlists-first-brand-character-to-support-franchisees/829215/"),
 ("치폴레, 신임 CBO 첫 캠페인서 크리에이터 100명에게 카메라 맡겨",
  "치폴레가 신임 최고브랜드책임자 페르난두 마샤두 취임 후 첫 캠페인으로 크리에이터 100명을 매장 50곳 주방에 투입해 신메뉴 폴로 아사도·칠리 라임 칩스를 소개하는, 브랜드 자체 촬영분 없는 콘텐츠를 선보였다.",
  "Marketing Dive", "https://www.marketingdive.com/news/chipotle-deploys-100-creators-for-latest-ads-spotlighting-fresh-food/829149/"),
 ("허쉬, 걸그룹 캣츠아이와 손잡고 Z세대 겨냥 신제품 캠페인",
  "허쉬가 신제품 솔티드 캐러멜·아포가토 크림바 홍보를 위해 걸그룹 캣츠아이를 기용, 히트곡 '핑키 업' 안무를 활용한 지하철 배경 광고를 TV·디지털·옥외광고로 전개한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-hersheys-katseye-campaign-takes-on-gen-zs-desire-for-indulgence/829013/"),
 ("숏폼 커머스 성공 공식 'P.I.C.K'... 다이소·무신사 사례 분석",
  "다이소 조회수 220만 회, 무신사 매출 1200억 원 등 숏폼 콘텐츠 성과 사례를 바탕으로 '공감 후킹-즉시 시연-가격 노출-구매 연결'이라는 숏폼 제작 공식을 제시했다.",
  "모비인사이드", "https://www.mobiinside.co.kr/2026/09/02/short-form-commerce-pick-framework/"),
 ("어도비, 인도 AI 마테크 스타트업 '릴로' 인수",
  "어도비가 자연어로 경쟁사 분석·콘텐츠 재가공·리드 생성 등 마케팅 업무를 자동화하는 인도 스타트업 릴로를 인수해 6인 팀과 기술을 확보했으며, 릴로의 단독 제품 서비스는 종료된다.",
  "TechCrunch", "https://techcrunch.com/2026/09/02/adobe-acquires-indian-market-intelligence-startup-rilo/"),
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
