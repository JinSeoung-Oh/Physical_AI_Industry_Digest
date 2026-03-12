"""
Physical AI Industry Digest → Google Chat
- 해외 + 국내 소스 수집
- Claude API로 사업적 관점 요약 + 중요도 랭킹
- Google Chat 웹훅으로 매일 전송

[변경 이력]
v3 (최신)
- 피드 진단 모드 추가 (--debug 플래그)
- 키워드 필터 2단계 구조: 광역 1차(is_relevant) → Claude 2차(rank_items)
- 소스별 최대 수집 수 제한 (MAX_PER_SOURCE)
- 불안정 피드 제거 (Bloomberg, FT, Reuters, Crunchbase)
- 안정적 피드 추가 (The Verge, Ars Technica, AI Business, MIT News Robotics)
- since 계산: 실행 시각 KST 기준 24시간 전
- 날짜 없는 기사 스킵
- 기사 없을 때 해외/국내 각각 구분 안내
"""

import os
import re
import sys
import json
import requests
import feedparser
import anthropic
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_CHAT_WEBHOOK = os.environ.get("GOOGLE_CHAT_WEBHOOK", "")

MAX_PER_SOURCE = 5    # 소스당 최대 수집 기사 수 (순서 편향 방지)
MAX_FETCH      = 40   # 전체 수집 후보 상한
MAX_ITEMS      = 10   # 최종 전송 (해외 5 + 국내 5)
KST = ZoneInfo("Asia/Seoul")

DEBUG_MODE = "--debug" in sys.argv  # python digest.py --debug

# ─────────────────────────────────────────────
# RSS 소스 - 해외
# 제거: Bloomberg(페이월), FT(페이월), Reuters(본문 없음), Crunchbase(RSS 불안정)
# 추가: The Verge AI, Ars Technica, AI Business, MIT News Robotics
# ─────────────────────────────────────────────
GLOBAL_SOURCES = [
    # ── 디버그 확인: OK ──
    {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "region": "해외",
    },
    {
        "name": "TechCrunch Robotics",
        "url": "https://techcrunch.com/category/robotics/feed/",
        "region": "해외",
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/feed/",
        "region": "해외",
    },
    {
        "name": "The Robot Report",
        "url": "https://www.therobotreport.com/feed/",
        "region": "해외",
    },
    {
        "name": "IEEE Spectrum Robotics",
        "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss",
        "region": "해외",
    },
    {
        "name": "Wired AI",
        "url": "https://www.wired.com/feed/tag/ai/latest/rss",
        "region": "해외",
    },
    {
        "name": "NVIDIA Blog",
        "url": "https://blogs.nvidia.com/feed/",
        "region": "해외",
    },
    {
        "name": "Financial Times Tech",
        "url": "https://www.ft.com/technology?format=rss",
        "region": "해외",
    },
    {
        "name": "Crunchbase News",
        "url": "https://news.crunchbase.com/feed/",
        "region": "해외",
    },
    {
        "name": "Bloomberg Tech",
        "url": "https://feeds.bloomberg.com/technology/news.rss",
        "region": "해외",
    },
    # ── 신규 추가 (디버그 미검증 → 다음 --debug 시 확인 필요) ──
    {
        "name": "The Verge AI",
        "url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
        "region": "해외",
    },
    {
        "name": "Ars Technica Technology",
        "url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "region": "해외",
    },
    {
        "name": "AI Business",
        "url": "https://aibusiness.com/rss.xml",
        "region": "해외",
    },
    # ── 제거: Reuters Technology (URLError — 피드 URL 죽음) ──
]

# ─────────────────────────────────────────────
# RSS 소스 - 국내
# ─────────────────────────────────────────────
KOREA_SOURCES = [
    # ── 디버그 확인: OK ──
    {
        "name": "전자신문",
        "url": "http://rss.etnews.co.kr/Section902.xml",  # 301 리다이렉트이나 정상 작동
        "region": "국내",
    },
    {
        "name": "IT조선",
        "url": "https://it.chosun.com/rss/allArticle.xml",
        "region": "국내",
    },
    {
        "name": "AI타임스",
        "url": "https://www.aitimes.com/rss/allArticle.xml",
        "region": "국내",
    },
    {
        "name": "인공지능신문",
        "url": "http://www.aitimes.kr/rss/allArticle.xml",
        "region": "국내",
    },
    {
        "name": "로봇신문",
        "url": "http://www.irobotnews.com/rss/allArticle.xml",
        "region": "국내",
    },
    {
        "name": "한국경제 IT",
        "url": "https://www.hankyung.com/feed/it",
        "region": "국내",
    },
    # ── URL 교체: ZDNet Korea (기존 404) ──
    {
        "name": "ZDNet Korea",
        "url": "https://zdnet.co.kr/rss/rss.aspx",  # kind 파라미터 제거 후 재시도
        "region": "국내",
    },
    # ── URL 교체: 블로터 (기존 404) ──
    {
        "name": "블로터",
        "url": "https://bloter.net/feed/",  # www 없이 재시도
        "region": "국내",
    },
    # ── 신규 추가 ──
    {
        "name": "디지털투데이",
        "url": "https://www.digitaltoday.co.kr/rss/allArticle.xml",
        "region": "국내",
    },
]

ALL_SOURCES = GLOBAL_SOURCES + KOREA_SOURCES

# ─────────────────────────────────────────────
# 필터 키워드
#
# [구조 변경]
# 기존: INCLUDE_KEYWORDS 하나로 정밀 매칭
# 변경: BROAD_KEYWORDS(광역, 1차) → Claude 랭킹(2차 정제)
#
# 이유: TechCrunch/VentureBeat는 "Figure raises $675M" 같은 표현을 쓰고
#       "VLA model", "physical ai" 같은 전문 용어는 잘 안 씀.
#       광역 키워드로 일단 많이 모아서 Claude가 걸러내는 방식이 더 효과적.
# ─────────────────────────────────────────────

# 1차 필터: 넓게 — AI/로봇 관련이면 일단 통과
# ─────────────────────────────────────────────
# 필터 키워드 설계 원칙
#
# 해외 매체(Bloomberg, FT 등)는 RSS summary가 짧거나 없는 경우가 많아
# 사실상 제목(title)만으로 필터링된다.
# → 1차 필터는 최대한 넓게, 2차 정제는 Claude 랭킹 프롬프트에 위임.
#
# 핵심 규칙:
# - 짧고 단순한 단어 위주 (복합 표현, 스페이스 패딩 X)
# - 제목에 자주 등장하는 표현 중심
# - 제외 키워드는 "확실히 무관한 것"만 최소화
# ─────────────────────────────────────────────

# 해외 광역 키워드: 단어 단위, 짧게
BROAD_KEYWORDS_EN = [
    # 로봇 기본
    "robot", "humanoid", "robotic", "autonomous",
    "embodied", "manipulation", "locomotion",
    # AI 기본 — " ai " 패딩 없이 단순 매칭
    "ai", "artificial intelligence", "machine learning",
    "foundation model", "large language model", "llm",
    # 투자/사업 이벤트
    "funding", "raises", "raised", "investment",
    "series a", "series b", "series c", "seed round",
    "ipo", "acquisition", "acquires", "merger", "partnership", "contract",
    "startup", "valuation",
    # 핵심 기업명 — 뉴스 제목에 실제로 나오는 짧은 형태
    "figure", "nvidia", "deepmind", "openai",
    "unitree", "ubtech", "agibot", "agility",
    "apptronik", "sanctuary", "skild",
    "boston dynamics", "optimus",
    "physical intelligence",
]

# 국내 광역 키워드
BROAD_KEYWORDS_KO = [
    "로봇", "인공지능", "자율",
    "휴머노이드", "매니퓰레이터",
    "투자", "펀딩", "시리즈", "상장", "ipo", "파트너십", "계약", "수주",
    "레인보우로보틱스", "두산로보틱스", "현대로보틱스",
    "삼성로봇", "lg로봇", "클로이",
    "뉴로메카", "로보티즈",
    "kaist", "kist",
    "피지컬ai", "피지컬 ai", "k-휴머노이드",
]

# 제외 키워드: 확실하게 무관한 것만, 최소화
EXCLUDE_KEYWORDS = [
    "cryptocurrency", "bitcoin", "ethereum",
    "stock market", "forex",
    "stable diffusion", "text-to-image",
    "CNC machining", "PLC controller",
]

def is_relevant(title: str, summary: str) -> bool:
    """
    1차 광역 필터.
    - 해외 매체는 summary가 짧거나 없으므로 title 기준으로도 통과 가능하도록 느슨하게.
    - 제외 키워드에 걸리면 즉시 탈락.
    - 광역 키워드 중 하나라도 있으면 통과 → Claude가 2차 정제.
    """
    title_lower   = title.lower()
    summary_lower = summary.lower()

    # 제외 키워드
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in title_lower or kw.lower() in summary_lower:
            return False

    # 광역 키워드 — title 단독으로도 체크 (summary 없는 피드 대응)
    all_broad = BROAD_KEYWORDS_EN + BROAD_KEYWORDS_KO
    for kw in all_broad:
        kw_lower = kw.lower()
        if kw_lower in title_lower:
            return True
        if kw_lower in summary_lower:
            return True

    return False


# ─────────────────────────────────────────────
# 피드 진단 모드
# python digest.py --debug 로 실행 시 각 소스 상태 출력
# ─────────────────────────────────────────────
def run_debug():
    """각 RSS 피드의 실제 작동 여부와 기사 수를 출력."""
    since = (datetime.now(KST) - timedelta(hours=24)).astimezone(timezone.utc)
    print(f"\n{'='*60}")
    print(f"[DEBUG] RSS 피드 진단 — since: {since.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*60}\n")

    for source in ALL_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            total_entries = len(feed.entries)

            # 24시간 이내 기사 수
            recent = 0
            no_date = 0
            relevant = 0
            for entry in feed.entries:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if not pub:
                    no_date += 1
                    continue
                pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                if pub_dt >= since:
                    recent += 1
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")[:500]
                    if is_relevant(title, summary):
                        relevant += 1

            status = "✅" if total_entries > 0 else "❌"
            print(
                f"{status} [{source['region']}] {source['name']}\n"
                f"   전체: {total_entries}건 | 24h이내: {recent}건 | "
                f"날짜없음: {no_date}건 | 필터통과: {relevant}건\n"
                f"   URL: {source['url']}\n"
            )
        except Exception as e:
            print(f"❌ [{source['region']}] {source['name']} — 오류: {e}\n")

    print(f"{'='*60}")
    print("[DEBUG] 진단 완료. 위 결과를 보고 피드 교체 여부를 판단하세요.")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────
# RSS 수집
# ─────────────────────────────────────────────
def fetch_items() -> tuple[list[dict], list[dict]]:
    since = (datetime.now(KST) - timedelta(hours=24)).astimezone(timezone.utc)
    print(f"[INFO] 수집 기준: {since.strftime('%Y-%m-%d %H:%M')} UTC 이후")

    global_items, korea_items = [], []
    seen = set()

    for source in ALL_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
        except Exception as e:
            print(f"[WARN] {source['name']} 수집 실패: {e}")
            continue

        source_count = 0

        for entry in feed.entries:
            if source_count >= MAX_PER_SOURCE:
                break

            title   = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", ""))[:1000]
            link    = entry.get("link", "")

            if not link or link in seen:
                continue
            seen.add(link)

            # 날짜 없는 기사 스킵
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if not published:
                continue

            pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
            if pub_dt < since:
                continue

            clean_summary = re.sub(r"<[^>]+>", " ", summary).strip()

            # 1차 광역 필터
            if not is_relevant(title, clean_summary):
                continue

            item = {
                "source":  source["name"],
                "region":  source["region"],
                "title":   title,
                "summary": clean_summary[:600],
                "url":     link,
            }

            if source["region"] == "해외":
                global_items.append(item)
            else:
                korea_items.append(item)

            source_count += 1

    print(f"[INFO] 1차 수집: 해외 {len(global_items)}개, 국내 {len(korea_items)}개")
    return global_items[:MAX_FETCH], korea_items[:MAX_FETCH]


# ─────────────────────────────────────────────
# Claude 2차 랭킹 (Physical AI 관련도 + 비즈니스 임팩트)
# ─────────────────────────────────────────────
def rank_items(items: list[dict], top_n: int, region: str) -> list[dict]:
    if not items:
        return []
    if len(items) <= top_n:
        return items

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    items_text = ""
    for i, item in enumerate(items, 1):
        items_text += f"[{i}] {item['title']}\n{item['summary'][:200]}\n---\n"

    prompt = f"""당신은 Physical AI / 휴머노이드 로봇 산업 전문 애널리스트입니다.
아래 {len(items)}개 ({region}) 기사 중, 두 가지 기준을 모두 충족하는 상위 {top_n}개를 선별하세요.

━ 기준 1: Physical AI / 로봇 관련도 ━
✅ 휴머노이드 로봇, 로봇 조작/자율주행, VLA/파운데이션 모델 적용
✅ NVIDIA Isaac / GR00T / Cosmos, Figure AI, Physical Intelligence, Skild AI 등
✅ 국내: 레인보우로보틱스, 두산로보틱스, 뉴로메카, K-휴머노이드 정책 등

❌ 순수 LLM/챗봇 (로봇 미적용), 반도체 설계, 스마트폰, 클라우드 인프라
❌ 일반 AI 투자 (로봇·Physical AI와 무관)

━ 기준 2: 비즈니스 임팩트 ━
✅ 투자 유치 / 펀딩 / IPO / M&A
✅ 제품 출시 / 상용화 / 파일럿 계약 / 양산
✅ 대형 파트너십 / 정부 정책·보조금
✅ 시장 판도를 바꿀 경쟁 구도 변화

❌ 학술 논문 (상용화 계획 없는 것)
❌ 단순 기술 벤치마크
❌ 이미 알려진 사실 반복

{items_text}

두 기준을 모두 반영해 중요도 높은 순서대로 {top_n}개 번호만 콤마로 출력.
예: 2,5,1,3,4
번호 외 다른 텍스트 없이."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        indices = [int(x.strip()) - 1 for x in msg.content[0].text.strip().split(",")]
        ranked = [items[i] for i in indices if 0 <= i < len(items)]
        return ranked[:top_n]
    except Exception as e:
        print(f"[WARN] 랭킹 파싱 실패: {e}")
        return items[:top_n]


# ─────────────────────────────────────────────
# Claude 요약 생성
# ─────────────────────────────────────────────
def summarize_items(items: list[dict]) -> list[dict]:
    if not items:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    items_text = ""
    for i, item in enumerate(items, 1):
        items_text += f"[{i}] {item['title']}\n내용: {item['summary']}\nURL: {item['url']}\n---\n"

    prompt = f"""당신은 Physical AI / VLA 산업 전문 애널리스트입니다.
아래 {len(items)}개 기사를 사업 담당자가 읽기 좋게 각각 요약해주세요.

각 기사마다 JSON 형식으로:
- "index": 번호
- "one_line": 핵심 내용 한 줄 (30자 이내, 임팩트 있게)
- "why_matters": 왜 사업적으로 중요한지 2문장
- "emoji": 내용에 맞는 이모지 1개

기술 용어보다 사업적 의미에 집중하세요.
학술적 표현 X, 쉽고 명확하게.

{items_text}

JSON 배열만 출력:
[{{"index":1,"one_line":"...","why_matters":"...","emoji":"..."}},...] """

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        text = msg.content[0].text.strip()
        text = re.sub(r"```json|```", "", text).strip()
        summaries = json.loads(text)
        for s in summaries:
            idx = s["index"] - 1
            if 0 <= idx < len(items):
                items[idx]["one_line"]    = s.get("one_line", "")
                items[idx]["why_matters"] = s.get("why_matters", "")
                items[idx]["emoji"]       = s.get("emoji", "📌")
    except Exception as e:
        print(f"[WARN] 요약 파싱 실패: {e}")
        for item in items:
            item.setdefault("one_line", item["title"])
            item.setdefault("why_matters", item["summary"][:100])
            item.setdefault("emoji", "📌")

    return items


# ─────────────────────────────────────────────
# Google Chat 메시지 전송
# ─────────────────────────────────────────────
def send_to_chat(global_items: list[dict], korea_items: list[dict]):
    today = datetime.now(KST).strftime("%Y.%m.%d")
    total = len(global_items) + len(korea_items)

    header_text = (
        f"🤖 *Physical AI & VLA 산업 동향* — {today}\n"
        f"해외 {len(global_items)}건 + 국내 {len(korea_items)}건 · 중요도 순 큐레이션"
    )

    def item_to_text(item: dict, rank: int) -> str:
        emoji    = item.get("emoji", "📌")
        one_line = item.get("one_line", item["title"])
        why      = item.get("why_matters", "")
        return (
            f"{emoji} *{rank}. {one_line}*\n"
            f"{why}\n"
            f"📰 {item['source']} · <{item['url']}|원문 보기>"
        )

    if global_items:
        global_section = "━━━ 🌐 *해외 동향* ━━━\n\n"
        for i, item in enumerate(global_items, 1):
            global_section += item_to_text(item, i) + "\n\n"
    else:
        global_section = f"━━━ 🌐 *해외 동향* ━━━\n\n📭 {today} 기준 해외 Physical AI 관련 새 기사가 없습니다.\n\n"

    if korea_items:
        korea_section = "━━━ 🇰🇷 *국내 동향* ━━━\n\n"
        for i, item in enumerate(korea_items, 1):
            korea_section += item_to_text(item, i) + "\n\n"
    else:
        korea_section = f"━━━ 🇰🇷 *국내 동향* ━━━\n\n📭 {today} 기준 국내 Physical AI 관련 새 기사가 없습니다.\n\n"

    if not global_items and not korea_items:
        full_text = f"🤖 *Physical AI Digest* — {today}\n📭 {today} 기준 새로운 기사가 없습니다."
    else:
        full_text = f"{header_text}\n\n{global_section}{korea_section}"

    payload = {"text": full_text}
    resp = requests.post(
        GOOGLE_CHAT_WEBHOOK,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=10,
    )

    if resp.status_code == 200:
        print(f"[INFO] Google Chat 전송 완료 ({total}건)")
    else:
        print(f"[ERROR] 전송 실패: {resp.status_code} {resp.text}")
        raise Exception(f"Chat webhook 실패: {resp.status_code}")


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if DEBUG_MODE:
        # 피드 진단만 실행 (Claude API / Webhook 호출 없음)
        run_debug()
        sys.exit(0)

    print("[START] Physical AI Chat Digest 시작")

    global_raw, korea_raw = fetch_items()

    global_top = rank_items(global_raw, top_n=5, region="해외")
    korea_top  = rank_items(korea_raw,  top_n=5, region="국내")

    global_top = summarize_items(global_top)
    korea_top  = summarize_items(korea_top)

    send_to_chat(global_top, korea_top)

    print("[DONE] 완료")
