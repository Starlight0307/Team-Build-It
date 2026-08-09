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
    cpu_temp = "측정 불가 (OS/하드웨어 미지원)"
    gpu_temp = "측정 불가 (OS/하드웨어 미지원)"
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
        return f"'{search_name}' 프로그램을 찾을 수 없거나 OS 권한 문제로 종료에 실패했습니다."
    
    