import requests
from bs4 import BeautifulSoup
import urllib.parse

# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "search_product_price": {
        "type": "function",
        "function": {
            "name": "search_product_price",
            "description": (
                "다나와에서 전자제품/상품의 최저가를 검색합니다. "
                "사용자가 제품명(아이폰, 맥북, 갤럭시, 노트북 등)과 함께 '얼마', '가격', '최저가'를 물어보면 이 함수를 호출하세요. "
                "예: '아이폰 15 얼마야?', '맥북 가격', 'RTX 4090 최저가' → search_product_price 호출 "
                "이것은 상품 가격 검색 함수입니다. 캘린더 일정 검색이 아닙니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 제품명. 예: '아이폰 15', '맥북 프로', '갤럭시 S24'"
                    }
                },
                "required": ["query"]
            }
        }
    }
}

def search_product_price(query: str = "", keyword: str = "") -> str:
    """다나와에서 상품의 최저가 정보를 검색하여 반환합니다."""
    # query 혹은 keyword 중 전달된 값을 검색어로 사용 (호환성 보장)
    search_query = query if query else keyword

    # 터미널 로그 (stderr로 출력)
    import sys
    sys.stderr.write(f"\n👀 [플러그인 실행] 다나와 가격 검색 작동! (검색어: {search_query})\n")
    sys.stderr.flush()

    if not search_query:
        return "검색어가 없습니다. 어떤 상품을 찾으시는지 말씀해주세요."

    # 다나와 검색 URL
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://search.danawa.com/dsearch.php?query={encoded_query}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # 상품 리스트 추출
        products = soup.select('li.prod_item')

        if not products:
            return f"'{search_query}'에 대한 검색 결과가 없습니다."

        results = []
        results.append(f"╔══════════════════════════════════════════════════════╗")
        results.append(f"║  🛒 '{search_query}' 최저가 검색 결과")
        results.append(f"╚══════════════════════════════════════════════════════╝\n")

        # 최대 5개 상품 정보 추출
        for idx, product in enumerate(products[:5], 1):
            try:
                # 상품명 추출
                name_elem = product.select_one('a.click_log_product_standard_title_')
                if not name_elem:
                    name_elem = product.select_one('p.prod_name a')

                if not name_elem:
                    continue

                name = name_elem.get_text(strip=True)

                # 가격 추출 - hidden input에서 가져오기
                price_formatted = "가격 정보 없음"

                # 방법 1: hidden input의 min_price
                price_input = product.select_one('input[id^="min_price_"]')
                if price_input and price_input.get('value'):
                    try:
                        price = int(price_input.get('value'))
                        price_formatted = f"{price:,}원"
                    except:
                        pass

                # 방법 2: 가격 텍스트에서 추출 (백업)
                if price_formatted == "가격 정보 없음":
                    price_elem = product.select_one('span.price_sect a strong')
                    if not price_elem:
                        price_elem = product.select_one('span.lwst_prc strong')
                    if not price_elem:
                        price_elem = product.select_one('strong.price')

                    if price_elem:
                        price_text = price_elem.get_text(strip=True).replace(',', '').replace('원', '')
                        try:
                            price = int(price_text)
                            price_formatted = f"{price:,}원"
                        except:
                            pass

                # 이미지 URL 추출
                img_elem = product.select_one('img')
                img_url = img_elem.get('src', '') if img_elem else ''

                # 상품 링크 추출
                if name_elem.get('href'):
                    link = name_elem.get('href')
                    if not link.startswith('http'):
                        link = "https://prod.danawa.com" + link
                else:
                    link = "링크 없음"

                # 카드 형식으로 출력
                results.append(f"┌─────────────────────────────────────────────────────┐")
                results.append(f"│ #{idx}")
                results.append(f"├─────────────────────────────────────────────────────┤")
                results.append(f"│ 📦 상품명:")
                results.append(f"│    {name[:50]}")
                if len(name) > 50:
                    results.append(f"│    {name[50:]}")
                results.append(f"│")
                results.append(f"│ 💰 최저가: {price_formatted}")
                results.append(f"│")
                results.append(f"│ 🔗 다나와 링크:")
                results.append(f"│    {link}")
                if img_url:
                    results.append(f"│")
                    results.append(f"│ 🖼️  이미지: {img_url}")
                results.append(f"└─────────────────────────────────────────────────────┘")
                results.append("")

            except Exception as e:
                continue

        if len(results) <= 3:
            return f"'{search_query}' 검색 결과를 가져오지 못했습니다."

        return "\n".join(results)

    except requests.exceptions.RequestException as e:
        print(f"[가격 검색] 통신 오류: {e}")
        return "⚠️ 가격 정보를 가져오지 못했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도해주세요."
    except Exception as e:
        print(f"[가격 검색] 오류: {e}")
        return "⚠️ 가격 검색 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."