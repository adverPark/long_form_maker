import json
import re
from typing import List
from pydantic import BaseModel, Field
from .base import BaseStepService
from apps.pipeline.models import Scene


# Pydantic 모델 정의 - Gemini 구조화 출력용
class SceneData(BaseModel):
    """씬 데이터 스키마"""
    scene_id: int = Field(description="씬 번호 (1부터 시작)")
    section: str = Field(description="섹션 (intro, body_1, body_2, body_3, action, outro)")
    duration_seconds: int = Field(description="씬 길이 (초)")
    narration: str = Field(description="자막에 표시될 대본 내용 - 반드시 원본 대본의 해당 부분을 그대로 포함")
    narration_tts: str = Field(description="TTS용 텍스트 - 숫자를 한글로 변환")
    image_prompt: str = Field(default="[PLACEHOLDER]", description="이미지 프롬프트 (항상 [PLACEHOLDER])")
    character_appears: bool = Field(description="캐릭터 등장 여부")


class SceneListResponse(BaseModel):
    """씬 목록 응답 스키마"""
    scenes: List[SceneData] = Field(description="분할된 씬 목록")


def number_to_korean(num_str: str) -> str:
    """정수를 한글로 변환"""
    digits = {'0': '영', '1': '일', '2': '이', '3': '삼', '4': '사',
              '5': '오', '6': '육', '7': '칠', '8': '팔', '9': '구'}
    num_str = num_str.replace(',', '')
    try:
        num = int(num_str)
    except ValueError:
        return num_str
    if num == 0:
        return '영'
    result = []
    units = [(1_0000_0000_0000, '조'), (1_0000_0000, '억'), (1_0000, '만'),
             (1000, '천'), (100, '백'), (10, '십')]
    for unit_val, unit_name in units:
        if num >= unit_val:
            unit_num = num // unit_val
            if unit_num == 1 and unit_name in ['천', '백', '십']:
                result.append(unit_name)
            else:
                result.append(number_to_korean(str(unit_num)) + unit_name)
            num %= unit_val
    if num > 0:
        result.append(digits[str(num)])
    return ''.join(result)


def convert_decimal_korean(num_str: str) -> str:
    """소수점 포함 숫자를 한글로"""
    digits = {'0': '영', '1': '일', '2': '이', '3': '삼', '4': '사',
              '5': '오', '6': '육', '7': '칠', '8': '팔', '9': '구'}
    num_str = num_str.replace(',', '')
    if '.' in num_str:
        integer, decimal = num_str.split('.')
        return number_to_korean(integer) + '점' + ''.join(digits.get(d, d) for d in decimal)
    return number_to_korean(num_str)


def convert_to_tts(text: str) -> str:
    """narration → narration_tts 변환 (숫자를 한글로)"""
    result = text
    suffix_map = {
        '%': '퍼센트', '조원': '조원', '조': '조', '억원': '억원', '억': '억',
        '만원': '만원', '만명': '만명', '만': '만', '원': '원', '년': '년',
        '월': '월', '일': '일', '개': '개', '명': '명', '배': '배', '대': '대',
        '곳': '곳', '개월': '개월', '위': '위', '호': '호', '번': '번',
    }
    ordered_suffixes = ['조원', '억원', '만원', '만명', '개월', '%', '조', '억', '만',
                        '원', '년', '월', '일', '개', '명', '배', '대', '곳', '위', '호', '번']
    # 소수점 + 단위
    for suffix in ordered_suffixes:
        pattern = rf'(\d+\.\d+)({re.escape(suffix)})'
        result = re.sub(pattern, lambda m: convert_decimal_korean(m.group(1)) + suffix_map.get(m.group(2), m.group(2)), result)
    # 정수 + 단위
    for suffix in ordered_suffixes:
        pattern = rf'([\d,]+)({re.escape(suffix)})'
        result = re.sub(pattern, lambda m: number_to_korean(m.group(1)) + suffix_map.get(m.group(2), m.group(2)), result)
    # 마이너스 처리
    result = re.sub(r'-(\d+)퍼센트', lambda m: '마이너스 ' + number_to_korean(m.group(1)) + '퍼센트', result)
    return result


class ScenePlannerService(BaseStepService):
    """씬 분할 서비스

    규칙:
    - 대본 전체가 빠짐없이 씬으로 분할되어야 함 (누락 금지!)
    - 초반 10개 씬: 10초 이내 (다이나믹하게)
    - 이후 씬: 15-20초 이내
    - 캐릭터 등장 30% 이상
    - image_prompt = "[PLACEHOLDER]" (image-prompter가 채움)
    - narration_tts: 숫자를 한글로 변환
    """

    agent_name = 'scene_planner'

    def execute(self):
        self.update_progress(5, '대본 로딩 중...')
        self.log('씬 분할 시작')

        # 입력 확인
        manual_input = self.get_manual_input()
        title = ''
        content = ''

        if manual_input:
            content = manual_input
            title = '사용자 입력 대본'
            self.log('수동 입력 대본 사용')
        elif hasattr(self.project, 'draft') and self.project.draft:
            draft = self.project.draft
            title = draft.title
            content = draft.content
            self.log(f'대본 로드: {title} ({len(content)}자)')

        if not content:
            raise ValueError('대본이 없습니다. 대본 작성을 먼저 완료하거나 직접 입력해주세요.')

        original_char_count = len(content)
        self.log(f'원본 대본 글자수: {original_char_count}자')

        # 프롬프트 구성
        self.update_progress(10, '프롬프트 준비 중...')
        prompt = self._build_prompt(title, content)

        # Gemini 호출 (Pydantic 구조화 출력)
        self.update_progress(20, 'AI 씬 분할 요청 중...')
        self.log('Gemini API 호출 중... (구조화 출력 모드)')
        try:
            response_data = self.call_gemini_json(prompt, SceneListResponse)
            scenes_data = response_data.get('scenes', [])
            self.log(f'Gemini 응답 수신: {len(scenes_data)}개 씬')
        except TimeoutError as e:
            self.log(f'❌ 씬 분할 실패: 타임아웃 - 대본이 너무 길거나 서버가 바쁨', 'error')
            raise ValueError(f'Gemini API 타임아웃: 대본 길이({len(content)}자)가 너무 길 수 있습니다. 잠시 후 다시 시도해주세요.')
        except Exception as e:
            self.log(f'❌ 씬 분할 실패: {type(e).__name__} - {str(e)[:200]}', 'error')
            raise

        # 검증
        self.update_progress(50, '결과 검증 중...')
        if not scenes_data:
            raise ValueError('씬 분할 결과가 비어있습니다.')
        self.log(f'파싱 결과: {len(scenes_data)}개 씬')

        # 검증 0: 나레이션 빈 씬 체크 (가장 중요!)
        empty_narration_count = sum(1 for s in scenes_data if not s.get('narration', '').strip())
        if empty_narration_count > 0:
            self.log(f'⚠️ 나레이션 없는 씬: {empty_narration_count}개', 'error')
            if empty_narration_count == len(scenes_data):
                # 모든 씬이 비어있으면 재시도
                self.update_progress(55, '나레이션 빈 씬 감지, 재분할 중...')
                self.log('모든 씬의 나레이션이 비어있음! 재시도...', 'error')
                scenes_data = self._retry_with_full_content(title, content, scenes_data)
                # 재시도 후에도 비어있으면 에러
                empty_after_retry = sum(1 for s in scenes_data if not s.get('narration', '').strip())
                if empty_after_retry == len(scenes_data):
                    raise ValueError('씬 분할 실패: 모든 씬의 나레이션이 비어있습니다. 프롬프트를 확인해주세요.')

        # 검증 1: 대본 누락 체크
        narration_total = sum(len(s.get('narration', '')) for s in scenes_data)
        diff = original_char_count - narration_total
        self.log(f'글자수 검증: 원본 {original_char_count}자, 씬 합계 {narration_total}자, 차이 {diff}자')

        if diff > 500:  # 500자 이상 누락 시 재시도
            self.update_progress(60, f'대본 누락 감지 ({diff}자), 재분할 중...')
            self.log(f'대본 누락! {diff}자 빠짐. 재시도...', 'error')
            scenes_data = self._retry_with_full_content(title, content, scenes_data)
            narration_total = sum(len(s.get('narration', '')) for s in scenes_data)
            self.log(f'재분할 결과: {len(scenes_data)}개 씬, {narration_total}자')

        # 검증 2: 캐릭터 등장 비율
        char_count = sum(1 for s in scenes_data if s.get('character_appears', False))
        char_ratio = char_count / len(scenes_data) if scenes_data else 0
        self.log(f'캐릭터 등장: {char_count}/{len(scenes_data)} ({char_ratio:.0%})')

        if char_ratio < 0.3:
            self.log('캐릭터 등장 30% 미만, 자동 보정 중...')
            scenes_data = self._adjust_character_appearance(scenes_data)

        # 검증 3: 씬 길이 체크
        self._validate_durations(scenes_data)

        # DB 저장
        self.update_progress(85, 'DB에 저장 중...')
        self.log('기존 씬 삭제 중...')
        self.project.scenes.all().delete()

        self.log('새 씬 저장 중...')
        for i, scene_data in enumerate(scenes_data):
            narration = scene_data.get('narration', '')
            # 숫자 → 한글 변환 (TTS용)
            narration_tts = convert_to_tts(narration)
            Scene.objects.create(
                project=self.project,
                scene_number=scene_data.get('scene_id', i + 1),
                section=self._normalize_section(scene_data.get('section', 'body_1')),
                narration=narration,
                narration_tts=narration_tts,
                duration=scene_data.get('duration_seconds', 10),
                has_character=scene_data.get('character_appears', False),
                image_prompt='[PLACEHOLDER]',
            )

        # 최종 검증 로그
        final_count = len(scenes_data)
        final_chars = sum(len(s.get('narration', '')) for s in scenes_data)
        final_char_ratio = sum(1 for s in scenes_data if s.get('character_appears', False)) / final_count

        self.log(f'씬 분할 완료', 'result', {
            'scene_count': final_count,
            'total_chars': final_chars,
            'original_chars': original_char_count,
            'char_diff': original_char_count - final_chars,
            'character_ratio': f'{final_char_ratio:.0%}'
        })
        self.update_progress(100, f'완료: {final_count}개 씬 ({final_chars}자)')

    def _build_prompt(self, title: str, content: str) -> str:
        """프롬프트 구성"""
        # DB에서 프롬프트 가져오기
        db_prompt = self.get_prompt()

        base_prompt = db_prompt if db_prompt else self._get_default_prompt()

        return f"""{base_prompt}

---

## 대본 (전체를 빠짐없이 씬으로 분할하세요!)

제목: {title}

{content}

---

⚠️ 가장 중요: narration 필드에 대본 내용을 반드시 포함!
- 원본 글자수: {len(content)}자
- 각 씬의 narration에 해당 구간의 대본 텍스트가 반드시 들어가야 함
- narration이 비어있으면 안 됨!
- 마지막 문장까지 빠짐없이!"""

    # 기본 프롬프트 (클래스 변수)
    DEFAULT_PROMPT = """# 씬 분할 전문가

대본을 씬으로 분할합니다.

## 🚨 절대 규칙 (반드시 지켜야 함!)

### 1. narration 필드 필수! (가장 중요!)
- 각 씬의 narration에 해당 구간의 대본 텍스트를 반드시 포함
- narration이 비어있으면 절대 안 됨!
- 대본의 모든 문장이 씬에 포함되어야 함
- 글자수 차이 100자 이내

### 2. 씬 길이
- 초반 10개 씬 (scene 1~10): 10초 이내 (다이나믹하게!)
- 이후 씬 (scene 11~): 15-20초 이내

### 3. 캐릭터 등장 30% 이상
- character_appears: true인 씬이 전체의 30% 이상

### 4. image_prompt = "[PLACEHOLDER]"
- 이미지 프롬프트는 작성하지 마세요

### 5. narration_tts
- narration을 TTS용으로 변환
- 숫자를 한글로 변환: 470% → 사백칠십퍼센트, 2024년 → 이천이십사년

## section 종류
- intro: 오프닝 (처음 8-10개 씬)
- body_1: 개념 설명
- body_2: 본질 분석
- body_3: 문제점 심화
- action: 액션 플랜
- outro: 마무리

## 필드 설명
- scene_id: 씬 번호 (1부터 시작)
- section: 섹션명
- duration_seconds: 씬 길이 (초)
- narration: 자막에 표시될 대본 내용 (필수! 비어있으면 안 됨!)
- narration_tts: TTS용 텍스트 (숫자를 한글로 변환)
- image_prompt: 항상 "[PLACEHOLDER]"
- character_appears: 캐릭터 등장 여부"""

    def _get_default_prompt(self) -> str:
        """기본 프롬프트 반환 (하위 호환성)"""
        return self.DEFAULT_PROMPT

    def _parse_response(self, response: str) -> list:
        """응답 파싱"""
        # JSON 블록 추출
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return data.get('scenes', data) if isinstance(data, dict) else data
            except json.JSONDecodeError:
                pass

        # 직접 JSON 파싱
        try:
            data = json.loads(response)
            return data.get('scenes', data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            pass

        # { } 블록 찾기
        json_match = re.search(r'\{[\s\S]*"scenes"[\s\S]*\}', response)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return data.get('scenes', [])
            except json.JSONDecodeError:
                pass

        self.log('JSON 파싱 실패', 'error')
        return []

    def _retry_with_full_content(self, title: str, content: str, partial_scenes: list) -> list:
        """대본 누락 시 재시도 (구조화 출력)"""
        retry_prompt = f"""이전 분할에서 대본 후반부가 누락되었습니다.

## 누락된 부분 포함해서 다시 전체 분할해주세요!

대본 전문:
{content}

마지막까지 빠짐없이 모든 문장을 씬으로 분할하세요.
원본 글자수: {len(content)}자

## 중요 규칙
- narration 필드에 원본 대본 내용을 그대로 포함해야 합니다
- 모든 문장이 빠짐없이 씬에 포함되어야 합니다"""

        try:
            response_data = self.call_gemini_json(retry_prompt, SceneListResponse)
            new_scenes = response_data.get('scenes', [])
            return new_scenes if new_scenes else partial_scenes
        except Exception as e:
            self.log(f'재시도 실패: {str(e)[:100]}', 'error')
            return partial_scenes

    def _adjust_character_appearance(self, scenes_data: list) -> list:
        """캐릭터 등장 비율 30% 이상으로 조정"""
        total = len(scenes_data)
        needed = int(total * 0.3)
        current = sum(1 for s in scenes_data if s.get('character_appears', False))

        if current >= needed:
            return scenes_data

        # 캐릭터 추가할 씬 선택 (질문, 강조, CTA 등)
        keywords = ['?', '할까요', '하세요', '입니다', '있습니다', '거든요', '잖아요']

        for scene in scenes_data:
            if current >= needed:
                break
            if not scene.get('character_appears', False):
                narration = scene.get('narration', '')
                if any(kw in narration for kw in keywords):
                    scene['character_appears'] = True
                    current += 1

        # 아직 부족하면 intro/outro에 추가
        for scene in scenes_data:
            if current >= needed:
                break
            if not scene.get('character_appears', False):
                if scene.get('section') in ['intro', 'outro']:
                    scene['character_appears'] = True
                    current += 1

        # 그래도 부족하면 간격 두고 추가
        no_char_count = 0
        for scene in scenes_data:
            if current >= needed:
                break
            if scene.get('character_appears', False):
                no_char_count = 0
            else:
                no_char_count += 1
                if no_char_count >= 3:  # 3개 연속 미등장이면 추가
                    scene['character_appears'] = True
                    current += 1
                    no_char_count = 0

        return scenes_data

    def _validate_durations(self, scenes_data: list):
        """씬 길이 검증 (로그만, 수정은 안 함)"""
        early_long = [s for s in scenes_data[:10] if s.get('duration_seconds', 0) > 10]
        if early_long:
            self.log(f'초반 10개 씬 중 {len(early_long)}개가 10초 초과', 'error')

        later_long = [s for s in scenes_data[10:] if s.get('duration_seconds', 0) > 20]
        if later_long:
            self.log(f'11번째 이후 씬 중 {len(later_long)}개가 20초 초과', 'error')

    def _normalize_section(self, section: str) -> str:
        """section 값 정규화"""
        valid_sections = ['intro', 'body_1', 'body_2', 'body_3', 'action', 'outro']
        section = section.lower().strip()

        # 매핑
        mappings = {
            'opening': 'intro',
            'introduction': 'intro',
            'body': 'body_1',
            'conclusion': 'outro',
            'ending': 'outro',
            'cta': 'outro',
        }

        if section in valid_sections:
            return section
        return mappings.get(section, 'body_1')
