import json
import re
from .base import BaseStepService
from apps.pipeline.models import Scene


class ImagePrompterService(BaseStepService):
    """이미지 프롬프트 작성 서비스

    핵심 원칙:
    - 주인공 캐릭터만: 심플한 웹툰 스타일
    - 나머지 전부: 현실적 + 컬러풀 (photorealistic, vibrant)
    - 대본 내용이 이미지만 봐도 이해되어야 함
    - 최소 30단어, 권장 50-80단어
    """

    agent_name = 'image_prompter'

    def execute(self):
        self.update_progress(5, '씬 로딩 중...')

        # 한글금지 옵션 확인 (체크박스 또는 텍스트 렌더링 불가 모델)
        no_text_option = self.execution.intermediate_data.get('no_text', False) if self.execution.intermediate_data else False
        image_model = getattr(self.project, 'image_model', 'gemini-3-pro')
        # Flash, FLUX, SDXL 등 텍스트 렌더링이 안 되는 모델들
        no_text_models = ['gemini-2.5-flash', 'flux-schnell', 'sdxl']
        no_text_model = image_model in no_text_models
        self.use_no_text = no_text_option or no_text_model

        if self.use_no_text:
            self.log('이미지 프롬프트 작성 시작 (한글금지 모드 - 텍스트 제외)')
        else:
            self.log('이미지 프롬프트 작성 시작 (일반 모드 - 텍스트 포함)')

        # DB에서 씬 가져오기
        all_scenes = list(self.project.scenes.all().order_by('scene_number'))

        if not all_scenes:
            raise ValueError('씬이 없습니다. 씬 분할을 먼저 완료해주세요.')

        total = len(all_scenes)
        self.log(f'총 {total}개 씬 로드')

        # 나레이션 검증 - 비어있으면 진행 불가
        scenes_without_narration = [s for s in all_scenes if not s.narration]
        if scenes_without_narration:
            empty_count = len(scenes_without_narration)
            self.log(f'나레이션 없는 씬: {empty_count}개', 'error')
            if empty_count == total:
                raise ValueError('모든 씬의 나레이션이 비어있습니다. 씬 분할을 다시 실행해주세요.')
            else:
                self.log(f'⚠️ {empty_count}개 씬의 나레이션이 비어있어 해당 씬은 건너뜁니다', 'warning')

        # 프롬프트가 필요한 씬만 필터링 (비어있거나 PLACEHOLDER이거나 너무 짧은 것)
        # 나레이션 없는 씬은 제외
        scenes_to_process = []
        for scene in all_scenes:
            if not scene.narration:
                continue  # 나레이션 없으면 건너뜀
            prompt = scene.image_prompt or ''
            if not prompt or prompt == '[PLACEHOLDER]' or len(prompt.split()) < 15:
                scenes_to_process.append(scene)

        if not scenes_to_process:
            self.log('모든 씬에 이미 프롬프트가 있습니다')
            self.update_progress(100, '완료: 처리할 씬 없음')
            return

        skipped = total - len(scenes_to_process)
        self.log(f'처리 대상: {len(scenes_to_process)}개 씬 (기존 프롬프트 {skipped}개 유지)')

        # 프롬프트 템플릿 가져오기
        prompt_template = self.get_prompt() or self._get_default_prompt()

        # 배치로 처리 (5개씩 - 더 디테일한 프롬프트를 위해)
        batch_size = 5
        processed = 0
        to_process_total = len(scenes_to_process)

        for i in range(0, to_process_total, batch_size):
            batch = scenes_to_process[i:i + batch_size]
            batch_end = min(i + batch_size, to_process_total)

            progress = 10 + int((i / to_process_total) * 80)
            scene_nums = [s.scene_number for s in batch]
            self.update_progress(progress, f'프롬프트 생성 중 (씬 {scene_nums})...')
            self.log(f'배치 처리: 씬 {scene_nums}')

            # 배치 프롬프트 생성
            prompts = self._generate_batch_prompts(batch, prompt_template)

            # DB 업데이트
            for j, scene in enumerate(batch):
                if j < len(prompts):
                    scene.image_prompt = prompts[j]
                    scene.save(update_fields=['image_prompt'])
                    processed += 1

        # 검증
        self.update_progress(95, '검증 중...')
        self._validate_prompts(all_scenes)

        self.log(f'이미지 프롬프트 완료', 'result', {
            'total_scenes': total,
            'processed': processed,
            'skipped': skipped
        })
        self.update_progress(100, f'완료: {processed}개 생성 ({skipped}개 유지)')

    def _get_default_prompt(self) -> str:
        """기본 시스템 프롬프트"""
        # 한글금지 모드면 텍스트 제외 프롬프트 사용
        if getattr(self, 'use_no_text', False):
            return self._get_flash_prompt()
        return self._get_pro_prompt()

    def _get_pro_prompt(self) -> str:
        """Pro 모델용 프롬프트 (한글 텍스트 포함)"""
        return """# 이미지 프롬프트 작성 전문가

대본(narration)을 분석하여 상황을 아주 디테일하게 묘사하는 이미지 프롬프트를 작성합니다.

## 핵심 원칙

- 대본 내용이 이미지만 봐도 이해되어야 함
- 캐릭터/스타일은 별도 참조 이미지로 제공됨 → 프롬프트에 캐릭터 외모 설명 넣지 말 것!
- 상황, 배경, 분위기, 감정을 디테일하게 묘사
- 컬러풀하게! 밋밋한 색상 금지

## 씬 유형별 공식

### 1. 데이터/통계 씬 (숫자, 퍼센트)
"Colorful infographic showing [주제]. Main visual: [차트/그래프]. Large bold text '[숫자]' with glowing effect. Korean text '[한글 라벨]' as subtitle. Color scheme: [감정 색상]. Modern vibrant infographic style."

### 2. 현장/실제 상황 씬
"Colorful realistic scene of [장소]. Setting: [구체적 환경]. Main subject: [피사체]. [상태/동작]. Style: photorealistic with vibrant color grading, cinematic quality. [조명]."

### 3. 역사/과거 사건 씬
"Historical documentary style, [시대]. Era: [날짜]. Setting: [장소]. Key visual: [핵심 이미지]. Style: vintage documentary, historical footage look. Color: [세피아/필름톤]."

### 4. 캐릭터 등장 씬 (has_character: true)
"Character as narrator (참조 이미지 제공됨). Expression: [표정]. Pose: [포즈]. Action: [동작]. Background: photorealistic [대본 내용에 맞는 배경], vibrant colors. Mood: [분위기]."

### 5. 개념/추상 설명 씬
"Conceptual visualization of [개념]. Visual metaphor: [비유]. Key elements: [구성요소]. Style: clean conceptual illustration, documentary quality. Color: [색상]. Dramatic lighting."

## 색상 가이드
- 위기/하락/경고: 빨강
- 성장/상승/희망: 초록
- 분석/설명/중립: 파랑
- 주의/변화: 주황
- 역사/과거: 세피아
- 미래/전망: 하늘색/보라

## 중요!
- 최소 30단어, 권장 50-80단어
- 영어로 작성
- ❌ 캐릭터 외모 설명 금지 (이미지로 제공됨)
- ✅ 상황/배경/분위기/감정/동작 묘사에 집중
- 추상적/모호한 표현 금지"""

    def _get_flash_prompt(self) -> str:
        """Flash/Replicate 모델용 프롬프트 (텍스트 완전 제외)"""
        return """# 이미지 프롬프트 작성 전문가 (NO TEXT MODE)

대본(narration)을 분석하여 상황을 아주 디테일하게 묘사하는 이미지 프롬프트를 작성합니다.

## 🚨 중요: 텍스트 없는 이미지 전용

**절대 금지:**
- ❌ 한글/영어 텍스트
- ❌ 숫자 텍스트
- ❌ "text showing...", "text saying..." 표현
- ❌ 캐릭터 외모 설명 (이미지로 제공됨)

**대신 사용:**
- ✅ 시각적 메타포 (그래프 모양, 화살표 방향)
- ✅ 색상으로 감정 표현 (빨강=위기, 초록=성장)
- ✅ 상황/배경/분위기/감정/동작 묘사

## 씬 유형별 공식

### 1. 데이터/통계 씬
"Colorful infographic visualization. Main visual: [3D 차트/그래프 모양]. Rising/falling bars/arrows. NO TEXT. Color scheme: [감정 색상]. Clean modern style."

### 2. 현장/실제 상황 씬
"Colorful realistic scene of [장소]. Setting: [구체적 환경]. Main subject: [피사체]. [상태/동작]. Style: photorealistic, cinematic quality. [조명]. NO TEXT."

### 3. 역사/과거 사건 씬
"Historical documentary style, [시대]. Setting: [장소]. Key visual: [핵심 이미지]. Sepia/film grain. NO TEXT."

### 4. 캐릭터 등장 씬 (has_character: true)
"Character as narrator (참조 이미지 제공됨). Expression: [표정]. Pose: [포즈]. Action: [동작]. Background: [배경], vibrant colors. Mood: [분위기]. NO TEXT."

### 5. 개념/추상 설명 씬
"Conceptual visualization of [개념]. Visual metaphor: [비유]. Key elements: [구성요소]. Color: [색상]. Dramatic lighting. NO TEXT."

## 색상으로 의미 전달
- 위기/하락: 빨강, 어두운 톤
- 성장/희망: 초록, 밝은 톤
- 분석/중립: 파랑
- 역사/과거: 세피아

## 중요!
- 최소 30단어, 권장 50-80단어
- 영어로 작성
- **NO TEXT IN IMAGE** 필수
- ❌ 캐릭터 외모 설명 금지
- ✅ 상황/배경/분위기/감정/동작 묘사에 집중"""

    def _generate_batch_prompts(self, batch: list, system_prompt: str) -> list:
        """배치로 프롬프트 생성"""
        # 씬 정보 구성
        scenes_info = []
        for scene in batch:
            scene_type = self._classify_scene(scene.narration)
            scenes_info.append(
                f"씬 {scene.scene_number} ({scene_type}, 캐릭터: {'있음' if scene.has_character else '없음'}):\n"
                f"대본: {scene.narration}"
            )

        scenes_text = "\n\n".join(scenes_info)

        user_prompt = f"""{system_prompt}

---

## 작성할 씬들:

{scenes_text}

---

각 씬에 대해 50-80단어의 디테일한 영어 이미지 프롬프트를 작성하세요.
대본 내용이 이미지로 명확하게 표현되어야 합니다.

출력 형식:
```
씬 [번호]:
[프롬프트]

씬 [번호]:
[프롬프트]
```"""

        response = self.call_gemini(user_prompt)

        # 파싱
        prompts = self._parse_prompts(response, len(batch))

        return prompts

    def _classify_scene(self, narration: str) -> str:
        """씬 유형 분류"""
        # 데이터/통계
        if re.search(r'\d+%|\d+억|\d+조|\d+만', narration):
            return '데이터'

        # 역사/과거
        if re.search(r'\d{4}년|과거|당시|그때', narration):
            return '역사'

        # 질문/설명
        if '?' in narration or narration.endswith('요') or narration.endswith('다'):
            return '설명'

        return '상황'

    def _parse_prompts(self, response: str, expected_count: int) -> list:
        """응답에서 프롬프트 파싱"""
        prompts = []

        # "씬 N:" 패턴으로 분리
        pattern = r'씬\s*\d+\s*[:\n]'
        parts = re.split(pattern, response)

        for part in parts[1:]:  # 첫 번째는 빈 문자열이거나 헤더
            # 다음 씬 시작 전까지의 텍스트
            prompt = part.strip()

            # 코드 블록 제거
            prompt = re.sub(r'```\w*\n?', '', prompt)
            prompt = prompt.strip()

            # 빈 줄로 끊기
            if '\n\n' in prompt:
                prompt = prompt.split('\n\n')[0]

            if prompt and len(prompt) > 20:
                prompts.append(prompt)

        # 부족하면 기본 프롬프트로 채우기
        default = "News documentary style scene. Professional broadcast quality, vibrant colors, modern infographic elements, clean composition, dramatic lighting."
        while len(prompts) < expected_count:
            prompts.append(default)

        return prompts[:expected_count]

    def _validate_prompts(self, scenes: list):
        """프롬프트 검증"""
        placeholder_count = 0
        short_count = 0

        for scene in scenes:
            prompt = scene.image_prompt or ''

            if prompt == '[PLACEHOLDER]' or not prompt:
                placeholder_count += 1
                self.log(f'씬 {scene.scene_number}: 프롬프트 없음', 'error')
            elif len(prompt.split()) < 20:
                short_count += 1
                self.log(f'씬 {scene.scene_number}: 프롬프트 짧음 ({len(prompt.split())}단어)', 'error')

        if placeholder_count > 0:
            self.log(f'미완료 프롬프트: {placeholder_count}개', 'error')
        if short_count > 0:
            self.log(f'짧은 프롬프트: {short_count}개', 'error')

        if placeholder_count == 0 and short_count == 0:
            self.log('모든 프롬프트 검증 통과')
