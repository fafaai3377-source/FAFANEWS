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
 ("문샷AI Kimi K2.7-Code 오픈소스 공개 — 1조 파라미터",
  "문샷AI가 6월 12일 1조 개 파라미터(활성 320억)를 갖춘 코딩 에이전트 모델 Kimi K2.7-Code를 오픈소스로 공개했다. 추론 토큰을 전작 대비 30% 절감하면서 코드 벤치마크에서 최대 31.5% 향상을 달성했다.",
  "Crypto Briefing", "https://cryptobriefing.com/kimi-k2-7-code-open-source-release/"),
 ("화웨이 HarmonyOS 7 공개 — 2,000개 AI 에이전트 연결",
  "화웨이가 HDC 2026에서 HarmonyOS 7 개발자 베타를 공개했다. AI 비서 Xiaoyi가 시스템 핵심으로 격상돼 2,000개 이상의 서드파티 에이전트와 2,100개 이상의 시스템 기능을 통합 조율한다.",
  "Pandaily", "https://pandaily.com/huawei-harmonyos-7-launch-hdc-2026-ai-agent-jun2026"),
 ("오라클, 노코드 프라이빗 에이전트 팩토리 출시",
  "오라클이 Oracle AI Database 26ai에 기업용 프라이빗 에이전트 팩토리를 추가했다. 데이터를 외부에 공유하지 않고도 비즈니스 분석가가 코딩 없이 전문 AI 에이전트를 구축·배포할 수 있다.",
  "Oracle Blog", "https://blogs.oracle.com/ai-and-datascience/oracle-private-agent-factory-mcp-multi-agent"),
 ("2026년 1분기 VC 투자 2,970억 달러 — AI가 81% 점유",
  "2026년 1분기 글로벌 벤처 투자가 2,970억 달러로 역대 최고치를 기록했다. AI 스타트업이 약 2,420억 달러(81%)를 흡수하며 AI IPO 슈퍼사이클이 시작됐다는 평가가 나온다.",
  "Tech Insider", "https://tech-insider.org/q1-2026-venture-capital-297-billion-ai-startup-funding-record/"),
 ("Anthropic, 650억 달러 조달 — 기업가치 9,650억 달러",
  "Anthropic이 5월 말 시리즈 H에서 650억 달러를 조달해 포스트머니 기업가치 9,650억 달러를 달성했다. OpenAI의 8,520억 달러를 넘어서며 민간 AI 기업 중 최고 평가액을 기록했다.",
  "The AI Insider", "https://theaiinsider.tech/2026/05/27/ai-funding-in-2026-where-venture-capital-is-going/"),
 ("세레브라스, IPO 첫날 68% 급등 — 시총 950억 달러",
  "AI 반도체 스타트업 세레브라스(CBRS)가 5월 14일 IPO에서 55.5억 달러를 조달하며 시가총액 950억 달러로 상장했다. 첫날 68% 급등하며 AI 하드웨어 부문 상장 열기를 높였다.",
  "AI Funding Tracker", "https://aifundingtracker.com/top-50-ai-startups/"),
 ("AI 에이전트 펀딩 버블 경고 — 토큰 비용에 자금 소진",
  "AI 에이전트 투자 열풍 이면에 엄중한 경고가 나온다. 초기 에이전트 스타트업 상당수가 과도한 모델 토큰 비용과 더딘 기업 도입으로 올 연말까지 자금이 고갈될 것으로 전망된다.",
  "Product Leaders Day", "https://productleadersdayindia.org/blogs/multi-agent-orchestration-news/ai-agent-startup-funding-news.html"),
]

DESIGN = [
 ("어도비, Mother Design과 브랜드 전면 개편",
  "어도비가 Mother Design과 협업해 1982년 이래 처음으로 브랜드 아이덴티티를 전방위 개편했다. 더 선명해진 Adobe 레드와 모듈형 타이포그래피 그리드, 긍정 공간의 'A'로 디지털 환경에 맞게 시스템을 재정비했다.",
  "Creative Boom", "https://www.creativeboom.com/news/reshaping-adobes-global-brand-identity-with-mother-design/"),
 ("재규어 Type 01 공개 — 전기차 전용 브랜드 원년",
  "재규어가 첫 번째 전기 럭셔리 GT 'Type 01'을 공개하며 완전 전기차 브랜드로의 전환을 공식화했다. 100년 전통의 '그로울러' 로고를 버리고 'Exuberant Modernism'을 내세운 새 아이덴티티를 도입했다.",
  "EV.com", "https://ev.com/news/jaguar-unleashes-its-electrified-future-a-bold-shift-to-ultra-luxury-evs"),
 ("Envato, 셔터스톡 인수 후 새 브랜드 아이덴티티 공개",
  "셔터스톡에 인수된 크리에이티브 마켓플레이스 Envato가 새 브랜드 아이덴티티를 발표했다. '크리에이티브 스파크' 심볼을 중심으로 에너지와 역동성을 담았으며 플랫폼 전반에 즉시 적용됐다.",
  "Creative Boom", "https://www.creativeboom.com/news/envato-launches-new-brand-identity/"),
 ("Brandon 에이전시, '에이전시 스피크' 벗고 새 정체성 선언",
  "사우스캐롤라이나 기반 에이전시 Brandon이 '통합 인사이트 성장 엔진'을 지향하는 리브랜드를 단행했다. 날카로운 타이포그래피와 생동감 있는 색상, 키네틱 레이아웃으로 역동적인 인상을 강조했다.",
  "Creative Boom", "https://www.creativeboom.com/news/brandon-drops-the-agency-speak-with-new-identity/"),
 ("2026 브랜딩, '이상하게' 가야 살아남는다",
  "AI가 평범한 산세리프 로고를 양산하는 지금, 손그림 타이포·노이즈 텍스처·임팩트 있는 개성이 브랜드를 구별짓는 핵심이 됐다. 크리에이티브 붐은 과도한 안전지향 디자인에서 벗어나야 한다고 경고한다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("칸 라이언즈 2026, '크리에이티브 브랜드 라이언' 신설",
  "칸 라이언즈 2026이 브랜드 창의성을 내부 역량·시스템·문화로 지속 생산하는 브랜드를 포상하는 '크리에이티브 브랜드 라이언'을 신설했다. 6월 22~26일 행사는 AI-인간 협업을 핵심 주제로 다룬다.",
  "Haute Living", "https://hauteliving.com/2026/06/cannes-lions-2026-introduces-the-creative-brand-lion-and-goes-all-in-on-ai/790426/"),
 ("2026 그래픽 트렌드: '잉크 고갈' 미학의 부상",
  "잉크가 다 떨어지는 것처럼 색상이 번지고 녹는 '경고 저잉크' 레이아웃이 2026 브랜드 디자인 트렌드로 급부상했다. 고정된 아이덴티티 대신 '살아 움직이는' 브랜드를 표현하는 방식으로 주목받는다.",
  "It's Nice That", "https://www.itsnicethat.com/features/forward-thinking-graphic-trends-2026-graphic-design-120126"),
]

MARKETING = [
 ("이퀴녹스 '모든 걸 의심하되 자신은 의심 말라' 캠페인",
  "럭셔리 피트니스 브랜드 이퀴녹스가 에이전시 Angry Gods와 AI 생성 비주얼을 인간 초상과 교차 배치한 'Question Everything but Yourself' 캠페인을 론칭했다. 디지털 이미지 신뢰 위기 시대에 '실제 몸'의 진정성을 역설했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/campaign-trail-equinox-uses-ai-to-contrast-fitness-with-digital-fakery/809381/"),
 ("AI 오버뷰 인용 브랜드, 오가닉 CTR 91% 우위",
  "구글 AI 오버뷰에 인용된 브랜드는 비인용 브랜드 대비 오가닉 CTR이 91% 높다는 데이터가 나왔다. 마케터들이 전통 SEO 대신 'GEO(Generative Engine Optimization)'로 전략을 전환해야 한다는 목소리가 커지고 있다.",
  "ALM Corp", "https://almcorp.com/blog/google-ai-overviews-organic-ctr-2026/"),
 ("Gen Z 53%, 구글 대신 TikTok·유튜브로 검색 시작",
  "Gen Z의 53%가 정보 탐색을 구글이 아닌 TikTok, Reddit, YouTube에서 시작한다는 조사 결과가 나왔다. 브랜드들이 소셜 검색 최적화 전략으로 신속히 전환해야 한다는 압박이 거세지고 있다.",
  "ALM Corp", "https://almcorp.com/blog/gen-z-tiktok-google-preference-drop-2026-data/"),
 ("칸 라이언즈 2026: AI 창의성이 화두, 심사 기준은 '인간적 사고'",
  "6월 22~26일 개막하는 칸 라이언즈 2026에서 AI와 인간 협업이 핵심 주제로 다뤄진다. 신설 'AI 크래프트' 서브카테고리가 도입됐고, 수상 심사 기준은 기술보다 '인간적 사고의 질'에 방점을 뒀다.",
  "Adweek", "https://www.adweek.com/brand-marketing/the-new-power-player-how-cannes-lions-is-grappling-with-ai/"),
 ("AWS, 칸 라이언즈서 광고·엔터테인먼트 에이전틱 AI 비전 제시",
  "AWS가 칸 라이언즈 2026에서 광고와 엔터테인먼트 산업의 에이전틱 AI 미래를 공개 발표했다. AI 에이전트가 크리에이티브 제작부터 미디어 집행까지 자동화하는 시나리오를 시연했다.",
  "Amazon Web Services", "https://aws.amazon.com/blogs/industries/aws-showcases-the-agentic-ai-future-of-advertising-and-entertainment-at-cannes-lions-2026/"),
 ("스머커 'Game Face' 캠페인, 월드컵 열기 공략",
  "J.M. 스머커 브랜드가 아르헨티나·브라질·콜롬비아·멕시코 팬덤 아이코노그래피를 활용한 'Game Face' 캠페인을 전개 중이다. 스포츠 마케팅과 식음료 브랜드의 결합 사례로 주목받는다.",
  "B2 The 7", "https://www.b2the7.com/news-blog/marketing-trends-june-15-2026"),
 ("단일 플랫폼 의존 금물 — 소셜 채널 분산화가 생존 열쇠",
  "단일 소셜 플랫폼 의존 전략의 위험성이 다시 부각됐다. 브랜드들은 이메일 리스트·직접 고객 관계·자체 채널을 강화해 알고리즘 변동이나 플랫폼 규제 리스크에 대비해야 한다는 분석이 나왔다.",
  "Seafoam Media", "https://seafoammedia.com/june-2026-marketing-news-trends-insights/"),
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
