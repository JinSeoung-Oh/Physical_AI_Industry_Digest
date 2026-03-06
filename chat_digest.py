"""
Physical AI Industry Digest → Google Chat
- 해외 5개 + 국내 5개 소스 수집
- Claude API로 사업적 관점 요약 + 중요도 랭킹
- Google Chat 웹훅으로 매일 전송

[수정 사항]
1. since 계산: DAYS_BACK + 1 → DAYS_BACK (범위 축소)
2. 날짜 없는 기사 스킵 (기존엔 날짜 없으면 필터 통과)
3. since 기준: 오늘 KST 자정 고정 (실행 시각 무관하게 오늘 기사만)
4. 기사 없을 때 메시지: 해외/국내 각각 구분해서 안내
"""

import os
import re
import json
import requests
import feedparser
import anthropic
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CHAT_WEBHOOK = os.environ["GOOGLE_CHAT_WEBHOOK"]

MAX_FETCH = 40   # 수집 후보
MAX_ITEMS = 10   # 최종 전송 (해외 5 + 국내 5)
KST = ZoneInfo("Asia/Seoul")

# ─────────────────────────────────────────────
# RSS 소스 - 해외
# ─────────────────────────────────────────────
GLOBAL_SOURCES = [
    {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
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
        "name": "MIT Technology Review AI",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "region": "해외",
    },
    {
        "name": "Wired AI",
        "url": "https://www.wired.com/feed/tag/ai/latest/rss",
        "region": "해외",
    },
]

# ─────────────────────────────────────────────
# RSS 소스 - 국내
# ─────────────────────────────────────────────
KOREA_SOURCES = [
    {
        "name": "전자신문",
        "url": "https://www.etnews.com/rss/allArticleRss.xml",
        "region": "국내",
    },
    {
        "name": "ZDNet Korea AI",
        "url": "https://zdnet.co.kr/rss/rss.aspx?kind=1",
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
        "name": "로봇신문",
        "url": "http://www.irobotnews.com/rss/allArticle.xml",
        "region": "국내",
    },
]

ALL_SOURCES = GLOBAL_SOURCES + KOREA_SOURCES

# ─────────────────────────────────────────────
# 필터 키워드 - PhysicalAI/VLA 산업 흐름
# ─────────────────────────────────────────────
INCLUDE_KEYWORDS = [
    # VLA / Physical AI 핵심
    "physical ai", "physical intelligence",
    "vision language action", "VLA",
    "embodied ai", "embodied intelligence",
    "humanoid", "휴머노이드",
    "foundation model", "robot learning",
    # 기업/산업 동향
    "Figure", "1X", "Agility", "Boston Dynamics",
    "Apptronik", "Sanctuary", "Unitree", "Fourier",
    "NVIDIA Isaac", "GR00T", "pi0", "openpi",
    "Tesla Optimus", "Optimus",
    # 투자/사업
    "로봇 투자", "로봇 스타트업", "robot startup",
    "robot investment", "robot funding",
    "AI robot", "AI 로봇",
    # 국내 기업
    "레인보우로보틱스", "두산로보틱스", "현대로보틱스",
    "삼성 로봇", "LG 로봇", "카카오 로봇",
    "네이버 로봇", "클로이",
]

EXCLUDE_KEYWORDS = [
    # 순수 하드웨어/제조
    "용접 로봇", "산업용 로봇 arm",
    "CNC", "PLC", "반도체 장비",
    "수술 로봇 장비", "surgical instrument",
    # 무관한 AI
    "챗봇", "chatbot", "text generation",
    "image generation", "stable diffusion",
    "stock market", "crypto",
]

def is_relevant(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in text:
            return False
    for kw in INCLUDE_KEYWORDS:
        if kw.lower() in text:
            return True
    return False

# ─────────────────────────────────────────────
# RSS 수집
# [수정] since = 오늘 KST 자정 기준 (실행 시각 무관)
# [수정] published 없는 기사는 스킵
# ─────────────────────────────────────────────
def fetch_items() -> tuple[list[dict], list[dict]]:
    # 오늘 KST 자정을 UTC로 변환해 기준으로 사용
    since = (
        datetime.now(KST)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )

    global_items, korea_items = [], []
    seen = set()

    for source in ALL_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
        except Exception as e:
            print(f"[WARN] {source['name']} 수집 실패: {e}")
            continue

        for entry in feed.entries:
            title   = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", ""))[:1000]
            link    = entry.get("link", "")

            if link in seen:
                continue
            seen.add(link)

            # [수정] 날짜 없으면 스킵 (기존엔 날짜 없으면 필터 통과)
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if not published:
                continue
            pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
            if pub_dt < since:
                continue

            clean_summary = re.sub(r"<[^>]+>", " ", summary).strip()

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

    print(f"[INFO] 수집: 해외 {len(global_items)}개, 국내 {len(korea_items)}개")
    return global_items[:MAX_FETCH], korea_items[:MAX_FETCH]

# ─────────────────────────────────────────────
# Claude 중요도 랭킹
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

    prompt = f"""PhysicalAI/VLA 산업 동향을 모니터링하는 사업 담당자입니다.
아래 {len(items)}개 ({region}) 기사 중 사업적으로 가장 중요한 {top_n}개 번호를 골라주세요.

높은 우선순위:
- 주요 기업의 VLA/Physical AI 제품 출시, 파트너십, 투자 유치
- 국내외 주요 기업 전략 변화
- 시장 판도를 바꿀 기술/정책 발표
- 국내 기업의 Physical AI 관련 동향

낮은 우선순위:
- 단순 기술 벤치마크
- 순수 하드웨어 스펙 발표
- 로봇 시장 통계/수치만 다루는 기사

{items_text}

중요한 순서대로 {top_n}개 번호만 콤마로 출력. 예: 2,5,1,3,4
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
# [수정] 해외/국내 각각 없을 때 구분 안내
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

    # 해외 섹션
    if global_items:
        global_section = "━━━ 🌐 *해외 동향* ━━━\n\n"
        for i, item in enumerate(global_items, 1):
            global_section += item_to_text(item, i) + "\n\n"
    else:
        global_section = "━━━ 🌐 *해외 동향* ━━━\n\n📭 {today} 날짜의 해외 최신 기사가 없습니다.\n\n".format(today=today)

    # 국내 섹션
    if korea_items:
        korea_section = "━━━ 🇰🇷 *국내 동향* ━━━\n\n"
        for i, item in enumerate(korea_items, 1):
            korea_section += item_to_text(item, i) + "\n\n"
    else:
        korea_section = "━━━ 🇰🇷 *국내 동향* ━━━\n\n📭 {today} 날짜의 국내 최신 기사가 없습니다.\n\n".format(today=today)

    # 둘 다 없으면 심플하게
    if not global_items and not korea_items:
        full_text = f"🤖 *Physical AI Digest* — {today}\n📭 {today} 날짜의 최신 기사가 없습니다."
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
    print("[START] Physical AI Chat Digest 시작")

    # 수집
    global_raw, korea_raw = fetch_items()

    # 랭킹 (해외 5개, 국내 5개)
    global_top = rank_items(global_raw, top_n=5, region="해외")
    korea_top  = rank_items(korea_raw,  top_n=5, region="국내")

    # 요약
    global_top = summarize_items(global_top)
    korea_top  = summarize_items(korea_top)

    # 전송
    send_to_chat(global_top, korea_top)

    print("[DONE] 완료")
