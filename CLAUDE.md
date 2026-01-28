# Long Form Maker 프로젝트

## 프로젝트 정보
- GitHub: https://github.com/adverPark/long_form_maker
- 프로젝트명: longform-web
- Python: 3.11+
- Django: 5.2.10

## 주요 의존성
- django>=5.2.10
- google-genai>=1.59.0
- google-generativeai>=0.8.6
- gunicorn>=24.1.1
- pillow>=12.1.0
- psycopg2-binary>=2.9.11
- python-dotenv>=1.2.1
- replicate>=1.0.0
- requests>=2.31.0
- cryptography>=46.0.3

## 서버 환경 주의사항
- 이 서버에는 `/home/adver/projects/mysite`에 advercoder_ai Django 백엔드가 존재함
- **반드시 독립된 가상환경(uv)을 사용하여 의존성 충돌 방지**
- 시스템 전역 패키지 설치 금지
- 다른 프로젝트의 설정 파일 수정 금지

## 설치 위치
- 경로: `/home/adver/long_form_site/`
- 가상환경: uv 사용 (프로젝트 내 `.venv`)

## 설치 완료 정보

### 접속
- URL: http://118.216.98.160:8000
- 관리자: adver

### 서비스
- 서비스명: longform
- 포트: 8000
- 가상환경: /home/adver/long_form_site/.venv

### 서비스 관리
```bash
sudo systemctl restart longform  # 재시작
sudo systemctl stop longform     # 중지
sudo systemctl status longform   # 상태확인
journalctl -u longform -f        # 로그 확인
rre                              # 재시작 alias (~/.bashrc)
```

### sudoers 설정 (비밀번호 없이 서비스 관리)
```
adver ALL=(ALL) NOPASSWD: /bin/systemctl restart longform, /bin/systemctl stop longform, /bin/systemctl start longform, /bin/systemctl status longform
```

### DB 정보
- DB명: longform
- DB유저: longform
- 호스트: localhost:5432

### TTS (Fish Speech + tts_wrapper)
- Fish Speech API: http://localhost:9880 (TTS 엔진)
- tts_wrapper API: http://localhost:9881 (TTS + 자막 생성)
- 경로: /home/adver/fish-speech
- 로그: /tmp/tts_wrapper.log

#### TTS 아키텍처
```
Django (tts_generator.py / views.py)
    ↓ HTTP POST (텍스트)
tts_wrapper.py (포트 9881)
    ↓ HTTP POST
Fish Speech API (포트 9880) → 오디오 생성
    ↓
tts_wrapper.py
    ↓ Forced Alignment (wav2vec2)
자막 타이밍 추출
    ↓
Django ← ZIP (audio.wav + subtitles.srt)
```

#### tts_wrapper 주요 기능
- **Forced Alignment**: 원본 텍스트 기준 단어별 타이밍 추출 (WhisperX align 모델 사용)
- **STT 미사용**: Whisper transcribe 모델 로드 안함 (속도↑, 메모리↓)
- **오디오 잘림 감지**: 타이밍 이상 감지 (단어 길이 < 0.05초)

#### tts_wrapper 관리
```bash
# 재시작
pkill -f 'python3 tts_wrapper.py'
cd /home/adver/fish-speech && nohup python3 tts_wrapper.py > /tmp/tts_wrapper.log 2>&1 &

# 로그 확인
tail -f /tmp/tts_wrapper.log

# 상태 확인
curl http://localhost:9881/v1/health
```

#### TTS 텍스트 전처리
Django에서 TTS 호출 전 특수문자 제거 (Fish Speech 호환성):
- 따옴표 제거: `'` `'` `"` `"` 등
- 말줄임표 정규화: `…` → `...`
- 위치: `apps/pipeline/services/tts_generator.py` (`_preprocess_for_tts`)
- 위치: `apps/pipeline/views.py` (`scene_generate_tts`)

### 기타 서비스 (이 서버)
- advercoder_ai: 8080 포트 (/home/adver/projects/mysite)

---

## 주요 기능

### 작업 목록 (Work List)
- 위치: 프로젝트 상세 페이지 상단
- 표시: 실행 중(running) + 실패(failed) 작업
- 기능:
  - 취소: 실행 중인 작업 취소 (DB status를 'cancelled'로 변경)
  - 다시: 실패한 작업 재실행
  - 삭제: 작업 기록 삭제
  - 로그: 상세 진행 상황 페이지로 이동

### 중복 실행 방지
- 같은 스텝이 이미 실행 중이면 새 실행 차단
- 기존 실행 진행 페이지로 리다이렉트

### AJAX 실행
- 스텝 실행 버튼 클릭 시 페이지 이동 없이 토스트 알림
- 1초 후 자동 새로고침으로 작업 목록 업데이트

### 수동 자료 입력 (Research)
- 리서치 섹션에서 수동으로 참고 자료 입력 가능
- 리서치 단계 없이 바로 대본 생성 가능
- 위치: `apps/pipeline/models.py` (Research.manual_notes 필드)

---

## 2025-01 업데이트 내역

### 1. Gemini 2.5 모델 추가
**파일**: `apps/accounts/models.py`, `apps/pipeline/services/base.py`, `apps/pipeline/views.py`

새로운 모델:
- `2.5-flash`: Gemini 2.5 Flash (저렴, 빠름)
- `2.5-pro`: Gemini 2.5 Pro (고품질, Google 검색 지원)

가격:
```python
'gemini-2.5-flash': {'input': $0.30, 'output': $2.50} / 1M 토큰
'gemini-2.5-pro': {'input': $1.25, 'output': $10.00} / 1M 토큰
'gemini-3-flash': {'input': $0.10, 'output': $0.40} / 1M 토큰
'gemini-3-pro': {'input': $1.00, 'output': $4.00} / 1M 토큰
```

### 2. 프로젝트별 모델 선택 UI
**파일**: `templates/pipeline/project_data.html`

- 각 스텝(리서치, 대본, 씬분할, 이미지프롬프트)마다 모델 선택 드롭다운
- 설정 페이지의 전역 모델 선택 제거

### 3. 자동 파이프라인 모델 선택 모달
**파일**: `templates/pipeline/project_data.html`, `apps/pipeline/views.py`, `apps/pipeline/services/auto_pipeline.py`

- 자동 생성 버튼 클릭 시 모달 표시
- 각 스텝별 모델 선택 가능
- `execution.intermediate_data['model_settings']`에 저장
- `AutoPipelineService.get_pipeline_steps()`에서 읽어서 적용

### 4. 병렬 처리 중 Unique Constraint 오류 수정
**파일**: `apps/pipeline/services/tts_generator.py`, `apps/pipeline/services/scene_generator.py`

문제:
- `scene.save()` 호출 시 Django ORM이 전체 필드 UPDATE
- 병렬 스레드에서 동시에 같은 씬 업데이트 시 충돌

해결:
```python
# 기존 (문제)
scene.audio.save(filename, ContentFile(data), save=True)

# 수정 (해결)
scene.audio.save(filename, ContentFile(data), save=False)
Scene.objects.filter(pk=scene.pk).update(audio=scene.audio.name)
```

### 5. 에러 카운트 기반 실패 판정
**파일**: `apps/pipeline/services/tts_generator.py`, `apps/pipeline/services/scene_generator.py`

- `error_count` 변수 추가
- 완료 시 `error_count > success_count`면 실패 처리
- 에러 있어도 일부 성공이면 완료 (경고 표시)

### 6. Stale Execution 정리 로직 수정
**파일**: `apps/pipeline/views.py`

문제:
- 로그 시간(KST)과 서버 시간(UTC) 비교 오류
- 방금 시작한 작업이 바로 실패 처리됨

해결:
```python
def _cleanup_stale_executions(user=None):
    stale_threshold = timezone.now() - timedelta(minutes=30)
    query = StepExecution.objects.filter(
        status='running',
        created_at__lt=stale_threshold
    )
```

### 7. 나레이션 빈 씬 검증
**파일**: `apps/pipeline/services/auto_pipeline.py`, `apps/pipeline/services/image_prompter.py`

- 씬 분할 후 나레이션 빈 씬 체크
- 모든 씬이 비어있으면 파이프라인 실패
- 이미지 프롬프트 생성 시에도 검증

```python
# auto_pipeline.py
if step_name == 'scene_planner':
    scenes_without_narration = self.project.scenes.filter(narration='').count()
    if scenes_without_narration == total_scenes:
        raise Exception('모든 씬의 나레이션이 비어있습니다.')
```

### 8. Pydantic 구조화 출력 (scene_planner)
**파일**: `apps/pipeline/services/scene_planner.py`

```python
from pydantic import BaseModel, Field

class SceneData(BaseModel):
    scene_id: int
    section: str
    duration_seconds: int
    narration: str = Field(description="자막에 표시될 대본 내용")
    narration_tts: str
    image_prompt: str = "[PLACEHOLDER]"
    character_appears: bool

class SceneListResponse(BaseModel):
    scenes: List[SceneData]

# 호출
response_data = self.call_gemini_json(prompt, SceneListResponse)
```

### 9. 커스텀 프롬프트 지원
**위치**: `apps/prompts/models.py`

사용 가능한 에이전트:
- `researcher`: 리서치
- `script_writer`: 대본 작성
- `scene_planner`: 씬 분할 (기본 프롬프트 DB 등록됨)
- `image_prompter`: 이미지 프롬프트

프롬프트 우선순위:
1. `UserAgentPrompt` (사용자별 커스텀)
2. `AgentPrompt` (시스템 기본, is_active=True)
3. 코드 내 기본 프롬프트

### 10. 모델 표시명 수정
**파일**: `templates/pipeline/step_progress.html`

```django
{% if execution.model_type == '2.5-flash' %}Gemini 2.5 Flash
{% elif execution.model_type == '2.5-pro' %}Gemini 2.5 Pro
{% elif execution.model_type == 'pro' %}Gemini 3 Pro
{% else %}Gemini 3 Flash{% endif %}
```

---

## 마이그레이션 파일

### accounts
- `0004_alter_user_gemini_model.py`: 모델 choices 업데이트
- `0005_add_gemini_25_models.py`: 2.5 모델 추가

### pipeline
- `0019_add_acknowledged_to_execution.py`: acknowledged 필드
- `0020_update_model_choices.py`: StepExecution 모델 choices 업데이트

### prompts
- `0002_useragentprompt.py`: 사용자별 프롬프트 모델
- `0003_add_scene_planner_default_prompt.py`: scene_planner 기본 프롬프트
