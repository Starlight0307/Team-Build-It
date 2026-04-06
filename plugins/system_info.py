import platform
import psutil

def get_system_info() -> str:
    """현재 컴퓨터의 운영체제, CPU 점유율, 메모리(RAM) 상태 정보를 확인하여 반환합니다."""
    print("\n👀 [플러그인 실행] GitHub에서 다운로드된 '시스템 진단' 모듈 작동!")
    os_info = f"{platform.system()} {platform.release()}"
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_total = round(ram.total / (1024**3), 2)
    ram_used = round(ram.used / (1024**3), 2)
    return f"OS: {os_info}, CPU 사용량: {cpu_usage}%, RAM: {ram_used}GB / {ram_total}GB 사용 중"