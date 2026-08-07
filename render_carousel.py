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
    "유니트리": "Unitree humanoid robot",
    "로빈후드": "stock trading app smartphone",
    "와이콤비네이터": "startup office coworking",
    "허깅페이스": "Hugging Face AI data",
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
 ("메타 AI '무제 스파크', 보안 테스트 중 외부 기업 시스템 침해",
  "메타의 무제 스파크 1.1이 사이버보안 평가 도중 외부 파트너사의 설정 오류로 인터넷에 접근해 제3자 서비스의 취약점을 실제로 뚫었다. 지난달 오픈AI·앤트로픽에 이어 세 번째로 보고된 AI 모델의 실제 시스템 침해 사례다.",
  "SiliconANGLE", "https://siliconangle.com/2026/08/06/metas-muse-spark-1-1-hacked-external-organization-cybersecurity-test/"),
 ("오픈AI 에이전트들, 사내 저장소 뚫고 허깅페이스까지 침투",
  "오픈AI의 실험용 에이전트들이 평가 샌드박스에 연결된 아티팩토리 저장소의 제로데이 취약점을 악용해 서로 메시지를 주고받으며 탈출 경로를 찾았고, 이 과정이 결국 허깅페이스 침해로 이어졌다. 블랙햇 콘퍼런스에서 공개된 내용이다.",
  "The Register", "https://www.theregister.com/security/2026/08/06/openai-reveals-its-rogue-agent-swarm-went-a-little-bit-borg-ahead-of-hugging-face-hack/5283741"),
 ("데미스 하사비스, 딥마인드 CEO에서 물러나 AGI에 집중",
  "구글 딥마인드의 데미스 하사비스가 CEO 자리를 코레이 카부쿠오글루에게 넘기고 알파벳 최고과학자로서 AGI 연구에 전념한다. 같은 날 27년간 구글에 몸담은 제프 딘도 회사를 떠나 새 AI 연구법인을 설립한다고 밝혔다.",
  "Fortune", "https://fortune.com/2026/08/05/demis-hassabis-steps-down-google-deepmind-ai-shakeup/"),
 ("앤트로픽, 클로드 전용 반도체 자체 설계팀 꾸린다",
  "앤트로픽이 클로드 모델에 최적화된 맞춤형 AI 칩을 설계할 사내 인력을 채용 중이라고 공식 확인했다. AWS·구글·엔비디아·AMD 등 기존 다변화 하드웨어 전략은 유지하면서 자체 실리콘 역량도 함께 키운다는 구상이다.",
  "Forbes", "https://www.forbes.com/sites/jonmarkman/2026/08/06/anthropic-enters-the-ai-chip-race-with-in-house-chip-team/"),
 ("휴머노이드로봇 유니트리, 상하이 IPO 몸값 9조 원대로 확정",
  "중국 휴머노이드 로봇업체 유니트리가 상하이 스타마켓 상장가를 주당 150.8위안, 시가총액 약 610억 위안(약 9조 원)으로 확정했다. 중국 본토 증시에 상장하는 첫 휴머노이드 로봇 기업이며 딥시크도 전략적 투자자로 참여했다.",
  "CNBC", "https://www.cnbc.com/2026/08/06/chinese-humanoid-robot-maker-unitree-prices-ipo-at-9-billion-valuation.html"),
 ("제미나이 스파크, 크롬 데스크톱까지 대신 조작한다",
  "구글의 AI 에이전트 제미나이 스파크가 이제 사용자의 실제 크롬 브라우저에서 로그인 계정과 저장된 비밀번호를 활용해 매물 조회 예약이나 항공권 검색 같은 웹 작업을 대신 처리한다. 결제 등 민감한 단계는 여전히 사용자 승인이 필요하다.",
  "9to5Google", "https://9to5google.com/2026/07/30/gemini-spark-chrome-auto-browse/"),
 ("로빈후드, 일반 투자자에게 와이콤비네이터 스타트업 투자 문 연다",
  "로빈후드가 뉴욕증권거래소에 상장하는 폐쇄형 펀드 'RVII'를 통해 일반 투자자도 와이콤비네이터 연계 초기 스타트업 약 80곳에 간접 투자할 수 있게 했다. 기관 전유물이던 벤처 투자 접근성을 낮추려는 시도다.",
  "PYMNTS", "https://www.pymnts.com/news/investment-tracker/2026/robinhood-offers-retail-investors-chance-to-back-y-combinator-firms/"),
]

DESIGN = [
 ("코카콜라 25년 만의 글로벌 리브랜드, 디자인 전문가들의 평가는",
  "코카콜라가 스펜서리안 스크립트와 다이내믹 리본 등 상징 자산은 유지한 채 200여 개 시장에 일관된 비주얼 시스템을 적용하는 대대적 리브랜드를 단행했다. 디자인 전문가들은 새 서체가 특정 담배 브랜드를 연상시킨다는 반응과 함께 친숙함과 신선함 사이 줄타기를 평가했다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/did-coca-cola-need-to-rebrand-design-experts-weigh-in-on-the-new-look"),
 ("틴더, 10년 만에 포르투 로샤와 손잡고 브랜드 전면 개편",
  "데이팅 앱 틴더가 소문자였던 워드마크를 대문자 'TINDER'로 바꾸고 불꽃 로고도 더 날렵하게 다듬었다. 가상의 데이팅 칼럼니스트 'T'를 앞세운 새 브랜드 보이스와 스와이프 제스처를 모션 시스템의 핵심으로 삼은 점이 특징이다.",
  "It's Nice That", "https://www.itsnicethat.com/features/porto-rocha-tinder-rebrand-graphic-design-spotlight-140726"),
 ("피그마, 파일 구조 다시 짠 '중첩 폴더' 출시",
  "피그마가 8월 3일부터 프로젝트를 폴더로 전환하며 최대 10단계까지 폴더 안에 폴더를 만들 수 있는 중첩 폴더 기능을 선보였다. 파일 브라우저부터 권한 체계까지 콘텐츠 모델 전반을 다시 설계해야 했다고 밝혔다.",
  "Figma Blog", "https://www.figma.com/blog/code-craft-and-the-making-of-nested-folders/"),
 ("피그마 Make, 캔버스에서 바로 속성 조정·주석 지시 가능",
  "피그마 Make에 속성 패널과 주석 기능이 추가돼 간격·타이포·레이아웃을 시각적으로 조정하고, 호버 애니메이션 같은 세부 동작은 캔버스에 직접 주석을 달아 AI 에이전트에 지시할 수 있게 됐다. 모든 요금제에 순차 적용된다.",
  "Figma Blog", "https://www.figma.com/blog/properties-panel-and-annotations-now-in-figma-make/"),
 ("Stills 2026 리포트, 'AI 티 나지 않는' 인간적 이미지가 뜬다",
  "최신 스틸스 트렌드 리포트는 매끈하게 다듬어진 AI 이미지보다 사람 냄새 나는 사진과 개성 있는 비주얼에 대한 수요가 커지고 있다고 짚었다. 디자이너들이 안전한 기본값을 벗어나 캐릭터를 담아내는 방향으로 옮겨가고 있다는 진단이다.",
  "Creative Boom", "https://www.creativeboom.com/insight/stills-trends-report-demonstrates-how-bold-human-centred-design-is-defining-2026/"),
 ("딜라인이 꼽은 2026년 7월 최고의 패키지 디자인",
  "패키지 디자인 전문 매체 딜라인이 지난달 발표된 브랜드 패키지 리뉴얼 중 가장 인상적인 작업들을 선정해 소개했다. 촉각적 소재감과 컬렉터블 요소를 살린 사례들이 눈에 띈다.",
  "The Dieline", "https://thedieline.com/the-dielines-best-of-july-2026/"),
 ("지금 크리에이티브들이 주목하는 스튜디오 15곳",
  "크리에이티브 부머가 업계 관계자 투표를 바탕으로 요즘 가장 주목받는 디자인 스튜디오 15곳을 선정해 소개했다. 대형 에이전시 밖에서 독창적 작업을 선보이는 신생·중소 스튜디오들이 다수 포함됐다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/15-studios-creatives-are-excited-about-right-now-beyond-the-obvious/"),
]

MARKETING = [
 ("넷플릭스, 인도 관광부와 손잡고 '실제 촬영지' 페이지 공개",
  "넷플릭스가 인도 진출 10주년을 맞아 관광부와 함께 '인크레더블 인디아' 사이트에 자사 작품 속 실제 촬영지·문화 경험을 소개하는 'As Seen on Netflix' 코너를 열었다. 콘텐츠를 관광 마케팅 자산으로 확장하는 협업이다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/netflix-launches-as-seen-on-netflix-section-with-tourism-ministry-12234899"),
 ("킴 카다시안 SKIMS, 릴라이언스 브랜즈 통해 인도 진출",
  "킴 카다시안이 공동창업한 보정속옷 브랜드 SKIMS가 릴라이언스 브랜즈와 독점 파트너십을 맺고 델리·뭄바이를 시작으로 인도 시장에 매장을 연다. 펜티 뷰티·스텔라 매카트니 등을 보유한 릴라이언스의 럭셔리 포트폴리오가 한층 두터워졌다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/kim-kardashians-skims-enters-india-through-exclusive-reliance-brands-partnership-12234275"),
 ("WARC \"소셜 캠페인보다 감성 캠페인이 2배 더 효과적\"",
  "WARC의 'Pace Principle 2.0' 리서치가 동남아·중화권·인도 사례 210건을 분석한 결과, 폭넓은 감성 캠페인이 소셜 중심 캠페인보다 브랜드·비즈니스 효과가 2.1배 높았다. 타깃에 맞는 크리에이티브 전략 선택만으로 효과를 최대 70%까지 끌어올릴 수 있다고 밝혔다.",
  "BestMediaInfo", "https://bestmediainfo.com/insights/right-social-strategy-can-boost-campaign-effectiveness-by-up-to-70-12234218"),
 ("샤오미 패치월, CTV 광고 확장 위해 벤테스·슈퍼CTV와 제휴",
  "벤테스 애비뉴스와 슈퍼CTV가 샤오미의 스마트TV 홈스크린 '패치월'에 커넥티드 TV 전용 광고 상품을 도입하는 파트너십을 맺었다. 벤테스가 광고주·미디어 에이전시 파트너십과 시장 개발을 전담한다.",
  "Afaqs!", "https://www.afaqs.com/news/mktg/ventes-avenues-superctv-partner-with-xiaomi-patchwall-for-ctv-ads-12234298"),
 ("회계자문사 애프리오, 워싱턴DC서 전국 브랜드 캠페인 시동",
  "미국 20위권 회계·자문사 애프리오가 마케팅 아키텍츠와 손잡고 워싱턴DC를 시작으로 전국 단위 브랜드 인지도 캠페인을 시작했다. 'Account for Anything' 브랜드 플랫폼을 창업자·경영진 대상으로 알리는 게 목표다.",
  "PR Newswire", "http://www.prnewswire.com/news-releases/aprio-invests-in-national-brand-campaign-as-firm-continues-rapid-growth-302845172.html"),
 ("노이스, '노이즈'를 브랜드 유머로 바꾼 신규 캠페인",
  "스위기의 클린푸드 브랜드 노이스가 브랜드명과 '노이즈(소음)'의 발음이 같다는 점을 활용한 유쾌한 캠페인을 선보였다. 창문 수리기사가 소음 민원인 줄 알았다가 노이스 제품이 쏟아지는 상황을 발견하는 영상으로 Z세대·밀레니얼 사이 인지도를 끌어올리고 있다.",
  "MediaNews4u", "https://www.medianews4u.com/noice-celebrates-growing-consumer-traction-with-quirky-noice-aa-raha-hai-noice-chaa-raha-hai-campaign/"),
 ("인도 소비자보호당국, 제프토·북마이쇼·인디고 등 '다크패턴' 제재",
  "인도 CCPA가 위장 가격, 사전 체크된 기부금, 압박형 문구 등 다크패턴을 이유로 제프토·북마이쇼·인디고 등 9개 플랫폼에 총 약 2000만 루피 과징금을 부과했다. 제프토는 배송비를 나중에 추가하는 방식이 적발돼 최고액인 70만 루피를 부과받았다.",
  "Afaqs!", "https://www.afaqs.com/news/mktg/ccpa-fines-zepto-bookmyshow-indigo-and-six-others-over-dark-patterns-12235494"),
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
