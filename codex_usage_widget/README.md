# Codex 사용량 미니 위젯 v2.5

서울·밴쿠버·UTC 시계와 Codex 5시간/주간 한도, 재설정권, OpenRouter 남은 크레딧을 표시하는 Windows용 위젯입니다.

## 실행

- `CodexUsageWidget_v2_5.exe`를 더블클릭합니다. Python 설치 없이 실행됩니다.
- `start_widget.bat`을 더블클릭합니다.
- 또는 `codex_usage_widget.py`를 Python으로 실행합니다.

위젯은 1분마다 자동 갱신됩니다. 드래그해서 옮길 수 있고, 위치는 자동 저장됩니다. 우클릭하면 수동 새로고침, 항상 위 표시, 종료 메뉴가 나타납니다.

OpenRouter 잔액은 5분마다 갱신됩니다. 맨 아래 OpenRouter 줄의 ⚙ 버튼이나 우클릭 메뉴에서 **OpenRouter 관리용 키**를 입력하세요. 기존 `4OpenRouter_Credit_Widget_실행용.pyw`에서 저장한 키도 그대로 읽습니다. 키는 현재 Windows 계정으로 암호화해 저장하며, GitHub 저장소에는 올라가지 않습니다. OpenRouter 줄에는 남은 금액만 표시합니다.

`사용 한도 재설정` 줄에는 남은 재설정권 수가 표시됩니다. 이 줄을 누르면 각 Full reset의 만료일이 펼쳐집니다. 안전을 위해 재설정권을 소비하지 않는 조회 전용 위젯입니다.

## 참고

- Codex 5시간/주간 한도는 로컬 기록을 즉시 표시한 뒤 계정의 최신 값으로 갱신합니다. 재설정권 정보는 Codex 로그인 정보를 이용해 ChatGPT 계정에서 조회합니다.
- OpenRouter 잔액은 관리용 키로 `/api/v1/credits`를 조회해 `total_credits - total_usage`로 계산합니다.
- 다른 위치의 Codex 데이터를 쓰는 경우 `CODEX_HOME` 환경 변수를 지정할 수 있습니다.
- Windows 시작 시 자동 실행하려면 `start_widget.bat`의 바로가기를 `Win+R` → `shell:startup` 폴더에 넣으세요.
