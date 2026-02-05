import re
from .base import BaseStepService
from apps.pipeline.models import Scene


def is_valid_comma_number(num_str: str) -> bool:
    """쉼표가 올바른 천 단위 구분자인지 확인 (예: 1,000 / 10,000 / 1,234,567)"""
    if ',' not in num_str:
        return True
    parts = num_str.split(',')
    # 첫 부분은 1-3자리, 나머지는 정확히 3자리여야 함
    if not (1 <= len(parts[0]) <= 3):
        return False
    for part in parts[1:]:
        if len(part) != 3:
            return False
    return True


def number_to_korean(num_str: str) -> str:
    """정수를 한글로 변환"""
    digits = {'0': '영', '1': '일', '2': '이', '3': '삼', '4': '사',
              '5': '오', '6': '육', '7': '칠', '8': '팔', '9': '구'}

    # 쉼표가 올바른 천 단위 구분자가 아니면 그대로 반환 (예: "2,3" → "2,3")
    if not is_valid_comma_number(num_str):
        return num_str

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


def number_to_native_korean(num: int) -> str:
    """숫자를 고유어 수사로 변환 (1-99만 지원, 그 이상은 한자어)"""
    if num <= 0 or num >= 100:
        return number_to_korean(str(num))

    native_units = ['', '한', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉']
    native_tens = ['', '열', '스물', '서른', '마흔', '쉰', '예순', '일흔', '여든', '아흔']

    tens = num // 10
    ones = num % 10

    result = native_tens[tens]
    if ones > 0:
        result += native_units[ones]

    return result


def convert_to_tts(text: str) -> str:
    """narration → narration_tts 변환 (숫자를 한글로)"""
    result = text

    # 고유어 수사를 쓰는 단위 (시간, 개, 명, 달, 살, 시 등)
    # 번/호/등/층/박 등은 한자어 (1번 버스=일번, 1호선=일호선, 1등=일등, 1층=일층, 1박=일박)
    native_suffixes = [
        '시간', '개', '명', '달', '살', '시',  # 시간/나이
        '마리', '잔', '병', '권', '장', '벌', '채', '대',  # 사물
        '그루', '송이', '톨', '알',  # 식물/작은것
        '곳', '군데', '가지', '끼', '켤레', '쌍', '주',  # 장소/종류/기타
    ]

    # 단위 표기 (고유어/한자어 공통)
    suffix_map = {
        '%': '퍼센트', '조원': '조원', '조': '조', '억원': '억원', '억': '억',
        '만원': '만원', '만명': '만명', '만': '만', '원': '원', '년': '년',
        '월': '월', '일': '일', '개': '개', '명': '명', '배': '배',
        '곳': '곳', '개월': '개월', '위': '위', '호': '호', '번': '번',
        '초': '초', '분': '분', '시간': '시간', '주': '주', '달': '달',
        '살': '살', '시': '시', '마리': '마리', '잔': '잔', '병': '병',
        '권': '권', '장': '장', '벌': '벌', '채': '채', '대': '대',
        '그루': '그루', '송이': '송이', '톨': '톨', '알': '알',
        '군데': '군데', '가지': '가지', '끼': '끼', '켤레': '켤레', '쌍': '쌍',
    }
    ordered_suffixes = ['조원', '억원', '만원', '만명', '개월', '시간', '%', '조', '억', '만',
                        '원', '년', '월', '일', '개', '명', '배', '곳', '위', '호', '번',
                        '초', '분', '주', '달', '살', '시', '마리', '잔', '병', '권', '장',
                        '벌', '채', '대', '그루', '송이', '톨', '알',
                        '군데', '가지', '끼', '켤레', '쌍']

    # 마이너스 + 숫자 + % 먼저 처리 (변환 전에)
    result = re.sub(r'-(\d[\d,]*)%', lambda m: '마이너스 ' + number_to_korean(m.group(1)) + '퍼센트', result)
    result = re.sub(r'-(\d+\.\d+)%', lambda m: '마이너스 ' + convert_decimal_korean(m.group(1)) + '퍼센트', result)

    # 고유어 수사 단위 먼저 처리 (1-99까지만)
    for suffix in native_suffixes:
        pattern = rf'(\d{{1,2}})({re.escape(suffix)})'
        def native_replace(m):
            num = int(m.group(1))
            if 1 <= num <= 99:
                return number_to_native_korean(num) + ' ' + suffix_map.get(m.group(2), m.group(2))
            else:
                return number_to_korean(m.group(1)) + suffix_map.get(m.group(2), m.group(2))
        result = re.sub(pattern, native_replace, result)

    # 소수점 + 단위
    for suffix in ordered_suffixes:
        pattern = rf'(\d+\.\d+)({re.escape(suffix)})'
        result = re.sub(pattern, lambda m: convert_decimal_korean(m.group(1)) + suffix_map.get(m.group(2), m.group(2)), result)
    # 정수 + 단위 (고유어 처리 안 된 것들)
    for suffix in ordered_suffixes:
        if suffix not in native_suffixes:
            pattern = rf'([\d,]+)({re.escape(suffix)})'
            result = re.sub(pattern, lambda m: number_to_korean(m.group(1)) + suffix_map.get(m.group(2), m.group(2)), result)
    return result


class ScenePlannerService(BaseStepService):
    """씬 분할 서비스 (규칙 기반 - LLM 미사용)

    규칙:
    - 문장 단위로 분리 후 글자수 제한 내에서 묶기
    - 초반 10개 씬: 최대 60자 (1문장)
    - 이후 씬: 최대 100자 (1-2문장)
    - 캐릭터 등장 30% 이상 (키워드 기반 + 비율 보정)
    - duration: 글자수 / 5 (한국어 약 5자/초)
    """

    agent_name = 'scene_planner'

    # 설정값
    EARLY_SCENE_COUNT = 10  # 초반 씬 개수
    EARLY_MAX_CHARS = 60    # 초반 씬 최대 글자수
    EARLY_MIN_CHARS = 50    # 초반 씬 최소 글자수
    NORMAL_MAX_CHARS = 100  # 일반 씬 최대 글자수
    NORMAL_MIN_CHARS = 80   # 일반 씬 최소 글자수
    CHARS_PER_SECOND = 5    # 초당 글자수 (TTS 기준)

    def execute(self):
        self.update_progress(5, '대본 로딩 중...')
        self.log('씬 분할 시작 (규칙 기반)')

        # 입력 확인
        manual_input = self.get_manual_input()
        content = ''

        if manual_input:
            content = manual_input
            self.log('수동 입력 대본 사용')
        elif hasattr(self.project, 'draft') and self.project.draft:
            draft = self.project.draft
            content = draft.content
            self.log(f'대본 로드: {len(content)}자')

        if not content:
            raise ValueError('대본이 없습니다. 대본 작성을 먼저 완료하거나 직접 입력해주세요.')

        # 대본 정리 (JSON이면 content 추출, 메타텍스트 제거)
        content = self._clean_draft_content(content)

        original_char_count = len(content)
        self.log(f'정리된 대본 글자수: {original_char_count}자')

        # 1. 문장 분리
        self.update_progress(20, '문장 분리 중...')
        sentences = self._split_sentences(content)
        self.log(f'문장 분리 완료: {len(sentences)}개 문장')

        # 2. 씬으로 묶기
        self.update_progress(40, '씬 구성 중...')
        scenes_data = self._group_into_scenes(sentences)
        self.log(f'씬 구성 완료: {len(scenes_data)}개 씬')

        # 3. section 할당
        self.update_progress(50, 'section 할당 중...')
        scenes_data = self._assign_sections(scenes_data)

        # 4. 캐릭터 등장 할당
        self.update_progress(60, '캐릭터 등장 할당 중...')
        scenes_data = self._assign_character_appearance(scenes_data)

        # 5. duration 계산
        self.update_progress(70, 'duration 계산 중...')
        scenes_data = self._calculate_durations(scenes_data)

        # 6. DB 저장
        self.update_progress(85, 'DB에 저장 중...')
        self.log('기존 씬 삭제 중...')
        self.project.scenes.all().delete()

        self.log('새 씬 저장 중...')
        for i, scene_data in enumerate(scenes_data):
            narration = scene_data['narration']
            Scene.objects.create(
                project=self.project,
                scene_number=i + 1,
                section=scene_data['section'],
                narration=narration,
                narration_tts='',  # TTS변환 스텝에서 별도 생성
                duration=scene_data['duration'],
                has_character=scene_data['character_appears'],
                image_prompt='[PLACEHOLDER]',
            )

        # 최종 로그
        final_chars = sum(len(s['narration']) for s in scenes_data)
        char_count = sum(1 for s in scenes_data if s['character_appears'])
        char_ratio = char_count / len(scenes_data) if scenes_data else 0

        self.log(f'씬 분할 완료', 'result', {
            'scene_count': len(scenes_data),
            'total_chars': final_chars,
            'original_chars': original_char_count,
            'char_diff': original_char_count - final_chars,
            'character_ratio': f'{char_ratio:.0%}'
        })
        self.update_progress(100, f'완료: {len(scenes_data)}개 씬 ({final_chars}자)')

    def _clean_draft_content(self, content: str) -> str:
        """대본 정리: JSON이면 content 추출, 메타텍스트 제거"""
        import json

        # 1. JSON 형식이면 content 필드만 추출
        if '```json' in content or ('"content"' in content and '"title"' in content):
            try:
                # ```json ... ``` 블록 추출
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                    content = data.get('content', content)
                else:
                    # 직접 JSON 파싱 시도
                    data = json.loads(content)
                    content = data.get('content', content)
            except (json.JSONDecodeError, Exception):
                pass

        # 2. 메타 텍스트 제거
        lines = content.split('\n')
        cleaned_lines = []

        meta_patterns = [
            r'^제시해주신',
            r'^요청하신',
            r'^다음은.*대본',
            r'^아래는.*대본',
            r'^네,\s*알겠습니다',
            r'^---$',
            r'^###\s*\[',
            r'^\*\*\(',
        ]

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # 메타 패턴 체크
            is_meta = False
            for pattern in meta_patterns:
                if re.match(pattern, stripped):
                    is_meta = True
                    break
            if not is_meta:
                cleaned_lines.append(line)

        content = '\n'.join(cleaned_lines).strip()
        return content

    def _split_sentences(self, text: str) -> list:
        """문장 단위로 분리"""
        # 줄바꿈을 공백으로 변환
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # 문장 끝 패턴: ., !, ?
        sentences = []

        # 문장 분리 정규식
        pattern = r'([^.!?]*[.!?])'

        parts = re.findall(pattern, text)
        remainder = re.sub(pattern, '', text).strip()

        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)

        # 남은 텍스트 처리
        if remainder:
            sentences.append(remainder)

        # 빈 문장 제거
        sentences = [s.strip() for s in sentences if s.strip()]

        return sentences

    def _group_into_scenes(self, sentences: list) -> list:
        """문장들을 글자수 제한 내에서 씬으로 묶기"""
        scenes = []
        current_narration = ""
        scene_count = 0

        for sentence in sentences:
            # 현재 씬이 초반인지 확인
            is_early = scene_count < self.EARLY_SCENE_COUNT
            max_chars = self.EARLY_MAX_CHARS if is_early else self.NORMAL_MAX_CHARS
            min_chars = self.EARLY_MIN_CHARS if is_early else self.NORMAL_MIN_CHARS

            # 현재 문장 추가 시 글자수
            potential_length = len(current_narration) + len(sentence) + (1 if current_narration else 0)

            if current_narration and potential_length > max_chars:
                # 최대 초과 → 현재 씬이 최소 이상이면 저장, 아니면 계속 합침
                if len(current_narration) >= min_chars:
                    scenes.append({'narration': current_narration.strip()})
                    scene_count += 1
                    current_narration = sentence
                else:
                    # 최소 미만이면 그냥 합침 (최대 초과해도)
                    current_narration += " " + sentence
            else:
                # 최대 이하 → 합침
                if current_narration:
                    current_narration += " " + sentence
                else:
                    current_narration = sentence

        # 마지막 씬 저장
        if current_narration:
            # 마지막 씬이 최소 미만이고 이전 씬이 있으면 합침
            min_chars = self.NORMAL_MIN_CHARS  # 마지막은 일반 기준
            if len(current_narration) < min_chars and scenes:
                scenes[-1]['narration'] += " " + current_narration.strip()
            else:
                scenes.append({'narration': current_narration.strip()})

        return scenes

    def _assign_sections(self, scenes_data: list) -> list:
        """위치 기반 section 할당"""
        total = len(scenes_data)
        if total == 0:
            return scenes_data

        for i, scene in enumerate(scenes_data):
            ratio = i / total

            if ratio < 0.1:  # 처음 10%
                scene['section'] = 'intro'
            elif ratio < 0.25:  # 10-25%
                scene['section'] = 'body_1'
            elif ratio < 0.5:  # 25-50%
                scene['section'] = 'body_2'
            elif ratio < 0.75:  # 50-75%
                scene['section'] = 'body_3'
            elif ratio < 0.9:  # 75-90%
                scene['section'] = 'action'
            else:  # 마지막 10%
                scene['section'] = 'outro'

        return scenes_data

    def _assign_character_appearance(self, scenes_data: list) -> list:
        """캐릭터 등장 할당 (키워드 기반 + 30% 비율 보정)"""
        # 캐릭터 등장 키워드
        keywords = ['?', '할까요', '하세요', '입니다', '있습니다', '거든요', '잖아요',
                    '그렇죠', '맞죠', '아니에요', '인데요', '네요', '죠']

        # 1차: 키워드 기반 할당
        for scene in scenes_data:
            narration = scene['narration']
            scene['character_appears'] = any(kw in narration for kw in keywords)

        # 2차: 30% 비율 보정
        total = len(scenes_data)
        needed = int(total * 0.3)
        current = sum(1 for s in scenes_data if s['character_appears'])

        # 부족하면 추가
        if current < needed:
            # intro/outro 우선
            for scene in scenes_data:
                if current >= needed:
                    break
                if not scene['character_appears'] and scene['section'] in ['intro', 'outro']:
                    scene['character_appears'] = True
                    current += 1

            # 그래도 부족하면 간격 두고 추가
            no_char_count = 0
            for scene in scenes_data:
                if current >= needed:
                    break
                if scene['character_appears']:
                    no_char_count = 0
                else:
                    no_char_count += 1
                    if no_char_count >= 3:
                        scene['character_appears'] = True
                        current += 1
                        no_char_count = 0

        return scenes_data

    def _calculate_durations(self, scenes_data: list) -> list:
        """글자수 기반 duration 계산"""
        for scene in scenes_data:
            chars = len(scene['narration'])
            # 글자수 / 초당 글자수, 최소 3초, 최대 25초
            duration = max(3, min(25, round(chars / self.CHARS_PER_SECOND)))
            scene['duration'] = duration

        return scenes_data
