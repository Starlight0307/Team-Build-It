import requests
from bs4 import BeautifulSoup

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