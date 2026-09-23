import platform
import psutil
import time
import os
import subprocess

# 💡 [핵심] 사용자가 "1번"이라고 했을 때 매칭할 수 있도록 파이썬이 리스트를 기억합니다.
LAST_TOP_PROCESSES = []

# ==========================================
# 🛠️ Tool Schemas (ollama tool calling용)
# ==========================================
TOOL_SCHEMAS = {
    "get_system_info": {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": (
                "현재 PC의 OS, CPU 코어 수 및 점유율, GPU, RAM 사용량, 디스크 여유 공간 등 "
                "시스템 상태 전체를 반환합니다. "
                "사용자가 '컴퓨터 상태', '내 PC 상태', '시스템 정보' 등을 물을 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    "get_top_cpu_processes": {
        "type": "function",
        "function": {
            "name": "get_top_cpu_processes",
            "description": (
                "CPU 점유율 상위 5개 프로세스 목록을 반환합니다. "
                "사용자가 '컴퓨터가 느리다', '왜 이렇게 무겁지', 'CPU 많이 쓰는 프로그램' 등을 물을 때 호출하세요. "
                "결과는 1~5번으로 번호가 매겨지며, 이후 kill_process 호출 시 번호로 참조됩니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    "kill_process": {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": (
                "지정한 프로세스를 강제 종료합니다. "
                "process_name_or_number에 프로세스 이름 또는 get_top_cpu_processes 결과의 번호(1~5)를 전달하세요. "
                "사용자가 '1번 종료해', '크롬 꺼줘' 등을 말할 때 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name_or_number": {
                        "type": "string",
                        "description": "종료할 프로세스 이름 또는 번호(1~5). 예: '1', '2', 'chrome.exe'"
                    }
                },
                "required": ["process_name_or_number"]
            }
        }
    }
}

def get_system_info() -> str:
    """현재 컴퓨터의 상세한 시스템 상태(OS, CPU 코어, RAM, 디스크, 온도 등)를 반환합니다."""
    print("\n👀 [플러그인] 상세 시스템 정보 스캔 중...")
    os_info = f"{platform.system()} {platform.release()}"
    cpu_cores = psutil.cpu_count(logical=True)
    cpu_usage = psutil.cpu_percent(interval=0.5)
    
    ram = psutil.virtual_memory()
    ram_total = round(ram.total / (1024**3), 1)
    ram_used = round(ram.used / (1024**3), 1)
    
    disk = psutil.disk_usage('/')
    disk_total = round(disk.total / (1024**3), 1)
    disk_free = round(disk.free / (1024**3), 1)
    
    # 💡 온도 및 GPU 정보 (Mac 등 지원 안되는 OS 예외 처리)
    cpu_temp = "측정 불가 (이 컴퓨터에서는 지원하지 않음)"
    gpu_temp = "측정 불가 (이 컴퓨터에서는 지원하지 않음)"
    gpu_info = "측정 불가"

    # 일반적인 온도 측정 시도
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            first_sensor = list(temps.values())[0]
            if first_sensor:
                cpu_temp = f"{first_sensor[0].current}°C"
    except:
        pass

    # Mac(Darwin) 환경일 경우 GPU 모델명 추출 시도
    if platform.system() == "Darwin":
        try:
            gpu_req = subprocess.check_output(["system_profiler", "SPDisplaysDataType"], text=True)
            for line in gpu_req.split('\n'):
                if "Chipset Model" in line or "Device Name" in line:
                    gpu_info = line.split(":")[1].strip()
                    break
        except:
            pass

    result = (
        f"[🖥️ 현재 컴퓨터 상태 상세 보고]\n"
        f"- 운영체제(OS): {os_info}\n"
        f"- CPU: {cpu_cores}코어 (점유율: {cpu_usage}% / 온도: {cpu_temp})\n"
        f"- GPU: {gpu_info} (온도: {gpu_temp})\n"
        f"- 메모리(RAM): 총 {ram_total}GB 중 {ram_used}GB 사용 중\n"
        f"- 디스크(Disk): 총 {disk_total}GB 중 {disk_free}GB 여유 공간"
    )
    return result


def get_current_cpu_percent() -> float:
    """plugins/reminder.py의 조건부 알림(cpu_limit)이 쓰는 내부 전용 함수 —
    TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수 없다. 정확히는
    "지금 이 순간의 CPU 사용률"이 아니라 "직전 측정 시점 이후 구간의 평균
    CPU 사용률"이다(아래 설명 참고).

    get_system_info()는 psutil.cpu_percent(interval=0.5)를 써서 0.5초 동안
    실측 블로킹한다 — 사용자가 "컴퓨터 상태 알려줘"라고 직접 물어봤을 때는
    한 번 0.5초 기다리는 게 자연스럽지만, 이 함수는 app_main.py의 30초
    주기 QTimer(메인/GUI 스레드에서 직접 실행됨)가 매번 호출하므로 그대로
    쓰면 폴링마다 GUI가 0.5초씩 멈추는 위험이 생긴다. 대신
    interval=None(직전 호출 이후 누적치를 논블로킹으로 즉시 반환)을 쓴다.

    ChatGPT 검수 지적(2026-09-23) + 실측 검증: psutil.cpu_percent()는 interval
    유무와 무관하게 "마지막 측정 시점" 기준점을 프로세스 전역으로 공유한다 —
    직접 재현해본 결과:
      interval=None 첫 호출              → 0.0 (문서화된 정상 동작)
      interval=0.5(블로킹) 호출 직후
      바로 interval=None 호출            → 0.0 (기준점이 0.5초 호출로 막 갱신돼서
                                              그 사이 경과 시간이 거의 없다고 계산됨)
      그로부터 1초 후 interval=None 호출  → 정상 값
    즉 사용자가 "컴퓨터 상태 알려줘"(get_system_info, 0.5초 블로킹 측정)를
    부른 직후의 조건부 알림 폴링 한 번은 CPU 사용률을 실제보다 낮게 읽을
    수 있다. ChatGPT 2차 검수 지적: 이걸 "최악의 경우 폴링 한 번만 놓치고
    반드시 다음 폴링에서 복구된다"는 보장으로 문서화하면 안 된다 — 위에서
    실측한 건 "이 프로젝트의 딱 이 호출 순서 하나"일 뿐이고, 다른 코드가
    추가로 cpu_percent()를 호출하거나 호출 간격이 달라지면 다른 결과가
    나올 수 있다. 정확한 서술은: "다른 CPU 측정 호출 직후에는 첫 논블로킹
    측정값이 부정확할 수 있고, 측정 기준점 이후 충분한 시간이 지나면 다시
    정상적인 값으로 돌아온다"는 것 — "반드시"가 아니라 "그럴 수 있다".
    토스트 알림용 편의 기능이라 이 정도 불확실성은 감수 가능하다고 판단해
    별도 보정 로직(예: 자체 cpu_times() 델타 추적)은 추가하지 않았다 —
    그렇게 하면 psutil의 기존 캐시와 별개의 상태를 새로 만드는 것이라
    오히려 두 기준점이 서로 안 맞는 복잡도가 늘어난다.

    더 일반적인 한계(2차 검수에서 추가로 지적): 이건 "다른 호출과의 간섭"
    문제와 별개로, 폴링 방식 자체의 근본적인 한계이기도 하다 — 앱이 켜져
    있어도 두 폴링(30초 간격) "사이"에 짧게 threshold를 넘었다가 다시
    내려간 경우는 애초에 감지할 수 없다(그 순간에 측정을 안 하니까). "앱이
    꺼져 있던 동안 놓침"은 이 더 일반적인 한계의 특수한 경우일 뿐이다 —
    set_cpu_condition/set_disk_condition의 사용자 안내 문구를 "폴링 사이
    일시적 조건은 놓칠 수 있다"로 일반화해서 반영했다."""
    return psutil.cpu_percent(interval=None)


def get_disk_free_percent(path: str = None) -> float:
    """plugins/reminder.py의 조건부 알림(disk_limit)이 쓰는 내부 전용 함수 —
    TOOL_SCHEMAS에 없으므로 AI 도구 호출로는 절대 불릴 수 없다. 여유 공간을
    "GB"가 아니라 "전체 대비 %"로 반환한다 — 디스크 용량은 사람마다 크게
    달라서(256GB짜리에 20GB 남은 것과 4TB짜리에 20GB 남은 것은 심각도가
    다름) 절대 용량 기준 임계값은 의미가 없다. path 생략 시 시스템 드라이브
    (get_system_info와 동일하게 '/'를 넘김 — psutil이 OS의 시스템 드라이브로
    해석한다. ChatGPT 검수 지적: 이게 "항상 C:\\"라고 단정하면 안 된다 —
    일반적인 Windows 환경에서는 C:지만, 시스템 드라이브가 다른 문자로
    설정된 환경도 이론적으로 가능하다. 그래서 사용자 안내 문구도 "C:"로
    못박지 않고 "시스템 드라이브"라고만 표현한다). 다른 드라이브/파티션은
    합산하지 않는다 — 여러 드라이브 여유공간이 필요하면 향후 path 인자를
    노출하는 확장이 필요하다(현재는 내부 전용이라 호출부에서 안 씀)."""
    try:
        disk = psutil.disk_usage(path or '/')
        return disk.free / disk.total * 100
    except Exception as e:
        print(f"[시스템 정보] 디스크 여유공간 조회 오류(조건부 알림용): {e}")
        return None


def get_top_cpu_processes() -> str:
    """CPU 점유율 상위 5개 프로그램 목록을 반환하고 메모리에 저장합니다."""
    global LAST_TOP_PROCESSES
    print("\n👀 [플러그인] CPU 점유율 정밀 측정 중...")

    # 측정 기준값 초기화 (첫 호출 시 0이 나오는 문제 방지)
    for proc in psutil.process_iter():
        try: proc.cpu_percent(interval=None)
        except: pass

    time.sleep(0.5)

    # 코어 수로 나눠서 0~100% 범위로 정규화
    cpu_count = psutil.cpu_count(logical=True) or 1

    # Windows 시스템 전용 프로세스 필터 (항상 수백%가 나와 의미 없음)
    EXCLUDED = {"system idle process", "idle", "system"}

    processes = []
    for proc in psutil.process_iter(['name']):
        try:
            name = proc.info['name'] or ""
            if name.lower() in EXCLUDED:
                continue
            # 코어 수로 나눠 실제 체감 점유율(0~100%)로 정규화
            cpu_val = proc.cpu_percent(interval=None) / cpu_count
            if cpu_val <= 0:
                continue
            processes.append({'name': name, 'cpu': cpu_val})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not processes:
        return "현재 CPU를 사용 중인 프로세스가 없습니다."

    processes.sort(key=lambda x: x['cpu'], reverse=True)
    top_5 = processes[:5]

    # 1번~5번 순서대로 프로그램 이름 저장 (kill_process에서 번호로 참조)
    LAST_TOP_PROCESSES = [p['name'] for p in top_5]

    result = "다음은 CPU를 가장 많이 사용하는 상위 5개 프로그램입니다:\n"
    for idx, p in enumerate(top_5, 1):
        result += f"{idx}. {p['name']} (점유율: {round(p['cpu'], 1)}%)\n"

    return result

def kill_process(process_name_or_number: str) -> str:
    """입력받은 이름이나 '번호(1~5)'에 해당하는 프로그램을 강제로 종료합니다."""
    global LAST_TOP_PROCESSES
    print(f"\n🔥 [플러그인] '{process_name_or_number}' 종료 시도 중...")
    
    search_name = process_name_or_number.strip()

    # 💡 사용자가 "1", "2" 등 숫자로 입력했을 경우, 기억해둔 리스트에서 이름을 빼옵니다.
    if search_name.isdigit():
        idx = int(search_name) - 1
        if 0 <= idx < len(LAST_TOP_PROCESSES):
            search_name = LAST_TOP_PROCESSES[idx]
        else:
            return "잘못된 번호입니다. 다시 확인해주세요."

    search_name_lower = search_name.lower()
    found = False
    killed_names = set()
    
    for proc in psutil.process_iter(['name']):
        try:
            p_name = proc.info['name']
            if p_name and search_name_lower in p_name.lower():
                proc.kill()
                killed_names.add(p_name)
                found = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
            
    if found:
        return f"성공적으로 {', '.join(killed_names)} 프로그램을 종료했습니다."
    else:
        return f"'{search_name}' 프로그램을 찾을 수 없거나 권한이 없어서 종료하지 못했습니다."
    
    