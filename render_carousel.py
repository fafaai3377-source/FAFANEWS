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
 ("Arcade.dev, 6,000만 달러 시리즈 A — AI 에이전트 보안 액션 레이어",
  "프로덕션 AI 에이전트의 보안 액션 레이어를 제공하는 Arcade.dev가 SYN Ventures 주도로 6,000만 달러 시리즈 A를 조달했다. 총 7,200만 달러 누적 조달로 엔터프라이즈 배포 확대와 인력 충원에 투자할 계획이다.",
  "PYMNTS", "https://www.pymnts.com/news/investment-tracker/2026/arcade-raises-60-million-to-control-ai-agents/"),
 ("OpenAI, 1억 5천만 달러 글로벌 파트너 네트워크 출범",
  "OpenAI가 기업용 AI 도입·배포·전환을 지원하는 글로벌 파트너 네트워크를 출범하며 1억 5천만 달러를 투자한다. 7월부터 운영을 시작하며 2026년 말까지 공인 컨설턴트 30만 명 육성을 목표로 한다.",
  "OpenAI", "https://openai.com/index/introducing-openai-partner-network/"),
 ("Anthropic, 연간 매출 300억 달러 돌파 — 1분기 80배 성장",
  "Anthropic이 1분기 매출이 80배 성장하며 연간 환산 매출이 300억 달러를 넘어섰다고 발표했다. Claude Code가 출시 6개월 만에 25억 달러 이상 연간 매출을 달성하며 성장을 견인했다.",
  "Anthropic", "https://www.anthropic.com/news/google-broadcom-partnership-compute"),
 ("Claude Fable 5, 마이크로소프트 Foundry·깃허브 코파일럿 탑재",
  "Anthropic의 Claude Fable 5가 Microsoft Azure Foundry와 GitHub Copilot에 통합됐다. 멀티스텝 장기 실행 작업에 최적화된 에이전트 우선 아키텍처로, 법률 리뷰·코드 리팩터링·연구 합성 등에 쓰인다.",
  "Microsoft Azure Blog", "https://azure.microsoft.com/en-us/blog/claude-fable-5-is-now-available-in-microsoft-foundry-powering-the-next-era-of-autonomous-agents/"),
 ("구글 Gemini 3.5 Flash 정식 출시 — 에이전틱 코딩 최강 Flash",
  "구글이 Gemini 3.5 Flash 정식 버전을 공개했다. 동급 모델 대비 4배 빠른 속도로 에이전틱·코딩 벤치마크에서 자사 Gemini 3.1 Pro를 처음으로 추월한 Flash 계열 모델이다.",
  "The Next Web", "https://thenextweb.com/news/google-gemini-3-5-flash-agentic-ai-coding-io-2026"),
 ("Inception, 확산 아키텍처 추론 LLM 'Mercury 2' 공개",
  "Inception이 자기회귀 방식 대신 확산(Diffusion) 아키텍처를 적용한 추론 모델 Mercury 2를 공개했다. NVIDIA Blackwell GPU에서 초당 1,009토큰을 처리하며 경쟁 LLM 대비 5배 이상 빠른 추론 속도를 기록했다.",
  "ModelsLab", "https://modelslab.com/inception-mercury-2"),
 ("OpenAI GPT-5.5 Instant, ChatGPT 기본 모델로 전환",
  "OpenAI가 GPT-5.5 Instant를 ChatGPT의 기본 모델로 전환했다. 고위험 분야에서 환각이 52.5% 감소했으며, 과거 대화·파일·Gmail을 참조한 개인화 응답 기능을 지원한다.",
  "TechCrunch", "https://techcrunch.com/2026/05/05/openai-releases-gpt-5-5-instant-a-new-default-model-for-chatgpt/"),
]

DESIGN = [
 ("Ideogram 4.0과 함께 공개된 How&How의 AI 아이덴티티 시스템",
  "생성형 AI 플랫폼 Ideogram이 4.0 버전과 함께 How&How가 제작한 새 브랜드 아이덴티티를 공개했다. 네거티브 스페이스의 'I'가 새겨진 뇌 형태 로고마크로, 아이디어와 판단의 주체는 여전히 인간임을 강조한다.",
  "Creative Review", "https://www.creativereview.co.uk/ideogram-brand-refresh-ai-how-and-how/"),
 ("'독서에 올인' — 영국 국립 독서의 해 2026 아이덴티티 공개",
  "Fold7Design이 영국 국립문해재단을 위해 '독서의 해 2026' 캠페인 아이덴티티를 제작했다. '열린 책(Open Book)'을 핵심 시각 요소로, 독서를 의무가 아닌 열정의 출구로 재프레이밍한다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_national_year_of_reading_2026_by_fold7design.php"),
 ("Interbrand, 자체 브랜드 인하우스로 직접 리디자인",
  "세계 최대 브랜드 컨설팅 그룹 중 하나인 Interbrand가 인하우스 팀으로 자체 아이덴티티를 전면 개편했다. 컨설팅사가 직접 자기 브랜드를 리디자인한 희귀한 사례로 디자인계의 주목을 받고 있다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_interbrand_done_in_house_2026.php"),
 ("핀란드 LAB 디자인대학, BOND의 '개혁가' 아이덴티티로 새 출발",
  "헬싱키 브랜드 컨설팅사 BOND가 핀란드 LAB 디자인·예술대학의 아이덴티티를 개편했다. '다르게 디자인하자'는 이중 의미의 핀란드어 문구를 축으로, 학생들이 로고 문자를 직접 변형·제작하는 살아있는 아이덴티티를 구현했다.",
  "Creative Review", "https://www.creativereview.co.uk/bond-muotoiluinstituutti-design-school-rebrand/"),
 ("Adobe, Mother Design과 함께 대담하고 붉은 새 아이덴티티",
  "Adobe가 Mother Design과 협업해 마바 워녹의 1982년 원본 디자인에 경의를 표한 새 로고타입을 발표했다. 기존의 분리된 'A' 아이콘과 워드마크를 하나로 통합하고 팔레트를 어도비 레드 중심으로 단순화했다.",
  "Creative Boom", "https://www.creativeboom.com/news/reshaping-adobes-global-brand-identity-with-mother-design/"),
 ("2026 UX·UI 트렌드: 멀티모달·에이전트 인터페이스로 진화",
  "UXPin이 정리한 2026년 최대 변화는 AI 생성이 일반 목업에서 팀 실제 컴포넌트 라이브러리 기반의 프로덕션 수준 UI로 진화한 것이다. 음성·텍스트·이미지를 결합한 멀티모달 인터페이스가 디자인 표준으로 자리잡고 있다.",
  "UXPin", "https://www.uxpin.com/studio/blog/ui-ux-design-trends/"),
 ("피그마, 캔버스 위 AI 에이전트로 디자인 자동화 본격화",
  "피그마가 Make와 Buzz 두 가지 AI 도구를 통해 디자인 캔버스에 에이전트를 내장했다. 로컬 코드베이스 연결부터 캠페인 자산 대량 편집까지, 디자인·개발 워크플로가 하나로 통합되고 있다.",
  "Figma Blog", "https://www.figma.com/blog/4-new-ways-to-go-from-idea-to-product-with-ai-tools/"),
]

MARKETING = [
 ("Salesforce Connections 2026: 모든 마케터에게 AI 마케팅팀",
  "세일즈포스가 Connections 2026에서 리드 발굴·콘텐츠 생성·캠페인 실행·성과 최적화를 수행하는 마케팅 AI 에이전트 세트를 공개했다. Brand Center도 6월부터 정식 제공되며 브랜드 보이스를 채널 전반에 자동 반영한다.",
  "Salesforce", "https://www.salesforce.com/news/stories/agentic-marketing-teams-announcement/"),
 ("FIFA 월드컵 2026 광고 전쟁: 아디다스·코카콜라·나이키의 대결",
  "6월 11일 개막한 FIFA 월드컵을 맞아 브랜드 광고 경쟁이 본격화됐다. 아디다스 'Backyard Legends', 코카콜라 'All the Feels', 나이키 'Rip the Script' 등이 팬덤 감정과 문화적 스토리텔링 전략을 앞세우고 있다.",
  "The Drum", "https://www.thedrum.com/news/world-cup-2026-watch-all-the-latest-ads"),
 ("Attentive Thread 2026: 에이전틱 AI로 1:1 마케팅 자동화",
  "Attentive가 Thread 2026에서 Brand Voice 2.0·Reporting Agent·Predictive Analytics·AI Campaigns를 공개했다. AI가 고객 신호를 분석해 메시지를 자동 생성·최적화하며 BFCM 2026을 겨냥한 캠페인 오케스트레이션을 지원한다.",
  "Attentive", "https://www.attentive.com/press-releases/attentive-unveils-next-generation-of-agentic-ai-marketing-innovation-at-thread-2026"),
 ("구글 마케팅 라이브 2026: Demand Gen과 AI 광고 도구 총출동",
  "구글이 마케팅 라이브에서 Gemini 기반 광고 스택을 전면 공개했다. Demand Gen이 새 AI 파워 캠페인으로 정식 론칭됐으며, AI Overviews·Search·유튜브 전반에 걸친 광고 통합이 강화됐다.",
  "Google", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("Centric AI Studio: PLM 연동 생성형 AI 제품 제작 플랫폼 출시",
  "Centric Software가 PLM 데이터와 직접 연동된 생성형 AI 제품 제작 플랫폼 Centric AI Studio를 출시했다. 패션·뷰티·식음료 브랜드의 스케치 생성부터 상업용 이미지까지 제품 라이프사이클 전반을 한 환경에서 처리한다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/centric-software-unveils-centric-ai-studio-powering-a-new-era-of-ai-driven-product-creation-302779406.html"),
 ("마테크 2026: AI 에이전트 도입률 90% — 업계 '리뉴얼' 전환점",
  "Scott Brinker 조사에 따르면 마케팅 조직의 90.3%가 AI 에이전트를 마테크 스택에 활용 중이다. 2026년 마테크 도구 수는 0.7% 소폭 증가에 그쳤지만 AI 통합과 스택 재편이 업계 핵심 키워드로 떠올랐다.",
  "MarTech", "https://martech.org/martech-2026-ai-drives-a-major-industry-reset/"),
 ("Adweek 50 선정 2026: 도전을 마주한 마케팅 리더들",
  "Adweek가 2026년판 '50인 마케팅 리더'를 발표했다. AI 전환, FIFA 월드컵, 경제 불확실성 속에서 브랜드를 이끄는 CMO와 마케팅 리더들의 전략과 성과가 조명됐다.",
  "Adweek", "https://www.adweek.com/brand-marketing/the-2026-adweek-50-are-up-for-the-challenge/"),
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
