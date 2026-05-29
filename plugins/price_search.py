import requests
from bs4 import BeautifulSoup

# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "search_product_price": {
        "type": "function",
        "function": {
            "name": "search_product_price",
            "description": (
                "다나와에서 상품의 최저가를 검색합니다. "
                "사용자가 '~~ 얼마야?', '~~ 최저가 알려줘', '~~ 가격 검색해줘' 등을 말할 때 호출하세요. "
                "keyword에는 검색할 상품명을 그대로 전달하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 상품명. 예: 'RTX 4090', '아이폰 15', '다이슨 청소기'"
                    }
                },
                "required": ["keyword"]
            }
        }
    }
}

def search_product_price(keyword: str) -> str:
    """사용자가 입력한 상품의 온라인 최저가 및 상품명을 검색하여 반환합니다."""
    print(f"\n👀 [플러그인 실행] GitHub에서 다운로드된 '가격 검색' 모듈 작동! (검색어: {keyword})")
    url = f"https://search.danawa.com/dSearch.php?k1={keyword}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    } 
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        first_product = soup.select_one('.product_list .prod_info')
        if not first_product:
            return f"'{keyword}' 검색 결과가 없거나 사이트에서 접근을 차단했습니다."
        name = first_product.select_one('.prod_name a').text.strip()
        try:
            price = first_product.select_one('.price_sect strong').text.strip()
        except AttributeError:
            price = "가격 정보 없음"
        return f"[가격 검색 성공] 상품명: {name} / 최저가: {price}원"
    except Exception as e:
        return f"웹 스크래핑 중 에러 발생: {e}"