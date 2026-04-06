이 프로젝트는 외부 API를 쓰지 않고 내 컴퓨터 안에서 구동되는 로컬 AI 에이전트입니다. 아래 순서대로 세팅해 주세요.

0. 필수 사전 준비

Anaconda (또는 Miniconda): 파이썬 가상환경 관리를 위해 필요합니다. 설치되어 있지 않다면 미리 설치해 주세요.

Git: 코드를 다운로드하기 위해 필요합니다.

1. 프로젝트 다운로드 (Clone)
윈도우 하단 시작 메뉴에서 **Anaconda Prompt**를 검색해서 실행한 뒤, 아래 명령어를 입력하여 코드를 다운로드하고 해당 폴더로 이동합니다.

<터미널 명령어> 

git clone https://github.com/Starlight0307/Team-Build-It

cd Team-Build-It

3. 독립된 파이썬 가상환경 생성 및 접속
패키지 충돌을 막기 위해 현재 프로젝트 전용 가상환경(방)을 만듭니다. (Anaconda Prompt에서 계속 진행합니다.)

<터미널 명령어>

conda create -n ai_agent python=3.10 -y

conda activate ai_agent


(💡 정상적으로 완료되면 터미널 맨 앞부분의 글자가 (base)에서 (ai_agent)로 바뀝니다!)

3. 필수 패키지 한 번에 설치
미리 세팅된 requirements.txt를 이용해 UI와 크롤링 등에 필요한 도구들을 설치합니다.

<터미널 명령어>

pip install -r requirements.txt


4. ⭐️ 핵심: Ollama 및 AI 모델 설치 (최초 1회)
이 프로그램은 구글 서버가 아닌 내 컴퓨터의 AI 엔진을 사용하므로, 윈도우용 AI 엔진 설치가 필수입니다.

Ollama 공식 홈페이지(ollama.com)에 접속하여 **Windows용 설치 파일(.exe)**을 다운로드하고 설치를 진행해 주세요. 
(설치가 완료되면 윈도우 우측 하단 작업 표시줄에 귀여운 '라마' 아이콘이 생깁니다.)

윈도우 시작 메뉴에서 일반 명령 프롬프트(cmd) 창을 새로 하나 열고, 아래 명령어를 입력하여 똑똑한 Llama 3.1 뇌를 다운로드합니다.

<터미널 명령어>

ollama run llama3.1

(💡 용량이 약 4.7GB이므로 시간이 조금 걸립니다. "Send a message..." 문구가 뜨면 다운로드가 완료된 것입니다. 이제 이 cmd 창은 닫으셔도 좋으나, 작업 표시줄의 라마 아이콘은 켜져 있어야 합니다.)

5. 대망의 프로그램 실행!
다시 아까 켜두었던 Anaconda Prompt 창 (맨 앞에 (ai_agent)라고 적혀있는 창)으로 돌아와서 메인 코드를 실행합니다.

<터미널 명령어>

python app_main.py

(💡 주의: 윈도우에서는 python3 대신 꼭 python 이라고 쳐야 합니다!)
