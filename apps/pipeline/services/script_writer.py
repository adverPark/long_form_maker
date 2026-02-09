import json
import re
from pydantic import BaseModel, Field
from .base import BaseStepService
from apps.pipeline.models import Draft, Research


class ScriptResponse(BaseModel):
    """대본 응답 스키마 - 구조화 출력 강제"""
    title: str = Field(description="영상 제목 (30자 이내)")
    content: str = Field(description="순수 대본 텍스트만. 타임스탬프, 섹션마커, 구분선, 메타설명 절대 금지. 시청자가 바로 들을 수 있는 나레이션만.")


class ScriptWriterService(BaseStepService):
    """대본 작성 서비스 - 8000자 분량의 영상 대본

    핵심: 가정 → 비유 → 숫자 → 위기 → 행동 공식
    """

    agent_name = 'script_writer'

    def execute(self):
        self.update_progress(5, '리서치 자료 분석 중...')

        # 입력 확인 (Research 모델 또는 수동 입력)
        manual_input = self.get_manual_input()
        research_data = self._prepare_research_data(manual_input)

        if not research_data:
            raise ValueError('리서치 자료가 필요합니다. 리서치 단계를 먼저 완료하거나 수동으로 입력해주세요.')

        self.update_progress(10, '대본 구조 설계 중...')

        # 대본 계획 + 리서치 결과 확인 로그
        content_analysis = research_data.get('content_analysis', {})
        script_plan = content_analysis.get('script_plan', '')
        research_result = content_analysis.get('research_result', '')

        self.log(f'대본 계획: {len(script_plan)}자' + (f' (처음 200자: {script_plan[:200]}...)' if script_plan else ' (없음)'))
        self.log(f'리서치 결과: {len(research_result)}자' + (f' (처음 200자: {research_result[:200]}...)' if research_result else ' (없음)'))

        # 시스템 프롬프트 (대본 작성 가이드 - DB에서만)
        system_prompt = self._build_system_prompt()
        self.log(f'시스템 프롬프트 길이: {len(system_prompt)}자')

        # 사용자 프롬프트 (리서치 자료 포함)
        user_prompt = self._build_user_prompt(research_data)
        self.log(f'사용자 프롬프트 길이: {len(user_prompt)}자')

        # Gemini 호출 (구조화 출력)
        self.update_progress(20, 'AI 대본 생성 중...')
        self.log('대본 생성 시작 (구조화 출력)')

        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

        try:
            response_data = self.call_gemini_json(full_prompt, ScriptResponse)
            draft_data = {
                'title': response_data.get('title', research_data.get('topic', '제목 없음')),
                'content': response_data.get('content', ''),
            }
        except Exception as e:
            self.log(f'구조화 출력 실패, 일반 호출로 재시도: {str(e)[:100]}', 'warning')
            response = self.call_gemini(full_prompt)
            draft_data = self._parse_response(response, research_data)

        # 대본 정리
        self.update_progress(60, '대본 정제 중...')
        draft_data['content'] = self._clean_content(draft_data['content'])

        # 글자수 확인 및 보강 (1회만 시도)
        char_count = len(draft_data['content'])
        self.log(f'초기 대본 글자수: {char_count}자')

        # 가장 긴 버전 = 초기 버전
        best_content = draft_data['content']
        best_count = char_count

        # 6000자 미만이면 1회 보강 시도 (목표: 8000자 이상)
        if char_count < 6000:
            needed = 8000 - char_count
            self.update_progress(70, f'글자수 부족 ({char_count}자), 보강 중...')
            self.log(f'보강 시도: {char_count}자 → +{needed}자 필요')

            try:
                # 대본 계획 + 리서치 결과 (마크다운 그대로)
                content_analysis = research_data.get('content_analysis', {})
                script_plan_text = content_analysis.get('script_plan', '')
                research_result_text = content_analysis.get('research_result', '')

                research_text = f"""## 대본 계획
{script_plan_text if script_plan_text else '(없음)'}

## 리서치 결과
{research_result_text if research_result_text else '(없음)'}"""

                self.log(f'보강에 전달: 대본계획 {len(script_plan_text)}자, 리서치결과 {len(research_result_text)}자')
                expanded = self._expand_content(draft_data['content'], needed, attempt=1, research_summary=research_text)
                expanded_count = len(expanded)

                self.log(f'보강 결과: {char_count}자 → {expanded_count}자')

                # 늘었으면 적용, 아니면 원본 유지
                if expanded_count > char_count:
                    draft_data['content'] = expanded
                    char_count = expanded_count
                    best_content = expanded
                    best_count = expanded_count
                    self.log(f'보강 성공: {best_count}자')
                else:
                    self.log(f'⚠️ 보강 실패 (짧아짐): {char_count}자 → {expanded_count}자. 원본 유지!', 'warning')

            except Exception as e:
                self.log(f'보강 중 오류: {str(e)[:200]}. 원본 유지!', 'error')

        # 최종: 무조건 가장 긴 버전
        draft_data['content'] = best_content
        char_count = best_count
        self.log(f'최종 대본: {char_count}자')

        if char_count < 6000:
            self.log(f'⚠️ 글자수 미달: {char_count}자', 'warning')

        # DB에 저장 (글자수 부족해도 반드시 저장)
        self.log(f'대본 저장 시작: {char_count}자')
        self.update_progress(90, '저장 중...')
        Draft.objects.update_or_create(
            project=self.project,
            defaults={
                'title': draft_data['title'],
                'content': draft_data['content'],
            }
        )

        self.log(f'대본 완료: {char_count}자', 'result', {
            'title': draft_data['title'],
            'char_count': char_count
        })
        self.update_progress(100, f'대본 완료: {char_count}자')

    def _prepare_research_data(self, manual_input: str) -> dict:
        """리서치 데이터 준비"""
        if manual_input:
            return {
                'topic': '사용자 입력 주제',
                'summary': manual_input,
                'quotes': [],
                'numbers': [],
                'person_stories': [],
                'time_change': {},
                'paradox': {},
                'best_title': {'title': '', 'hook': ''},
            }

        # Research 모델이 있는지 확인 (DB에서 최신 데이터 직접 읽기 - ORM 캐시 회피)
        r = Research.objects.filter(project=self.project).first()
        if not r:
            return None

        # manual_notes만 있는 경우 (리서치 실행 안하고 수동 자료만 입력한 경우)
        if r.manual_notes and not r.summary:
            topic = r.topic or (self.project.topic.title if hasattr(self.project, 'topic') and self.project.topic else '수동 입력 주제')
            return {
                'topic': topic,
                'summary': r.manual_notes,
                'quotes': [],
                'numbers': [],
                'person_stories': [],
                'time_change': {},
                'paradox': {},
                'best_title': {'title': '', 'hook': ''},
            }

        # 리서치 데이터가 전혀 없으면 None (summary, manual_notes, quotes, numbers, content_analysis 등 모두 체크)
        has_data = (
            r.summary or
            r.manual_notes or
            r.quotes or
            r.numbers or
            r.article_summaries or
            r.content_analysis or  # YouTube 분석 결과
            r.transcript  # YouTube 자막
        )
        if not has_data:
            return None

        # 기존 리서치 데이터 + manual_notes 조합
        data = {
            'topic': r.topic or '',
            'summary': r.summary or '',
            'quotes': r.quotes or [],
            'numbers': r.numbers or [],
            'person_stories': r.person_stories or [],
            'time_change': r.time_change or {},
            'paradox': r.paradox or {},
            'best_title': r.best_title or {},
            'viewer_connection': r.viewer_connection or {},
            'narrative_structure': r.narrative_structure or {},
            'article_summaries': r.article_summaries or [],
        }

        # content_analysis가 있으면 추가 (YouTube 기반 분석)
        if r.content_analysis:
            data['content_analysis'] = r.content_analysis
            # draft_outline이 있으면 narrative_structure로 변환
            draft_outline = r.content_analysis.get('draft_outline', {})
            if draft_outline and not data['narrative_structure']:
                data['narrative_structure'] = {
                    'intro': {'hook': draft_outline.get('hook', ''), 'setup': draft_outline.get('intro', '')},
                    'body_points': draft_outline.get('body_points', []),
                    'conclusion': {'summary': draft_outline.get('conclusion', '')},
                }
            # viewer_interests를 viewer_connection으로
            if r.content_analysis.get('viewer_interests') and not data['viewer_connection']:
                data['viewer_connection'] = {
                    'viewer_interests': r.content_analysis.get('viewer_interests', []),
                }
            # 주요 댓글
            if r.content_analysis.get('highlight_comments'):
                data['highlight_comments'] = r.content_analysis.get('highlight_comments', [])

        # 원본 자막이 있으면 참고용으로 추가
        if r.transcript:
            data['transcript'] = r.transcript[:5000]  # 최대 5000자

        # manual_notes가 있으면 summary에 추가
        if r.manual_notes:
            data['manual_notes'] = r.manual_notes
            # summary에 manual_notes 내용 추가
            if data['summary']:
                data['summary'] = data['summary'] + '\n\n--- 수동 추가 자료 ---\n' + r.manual_notes
            else:
                data['summary'] = r.manual_notes

        return data

    # 기본 프롬프트 (DB에 없을 때 사용)
    DEFAULT_PROMPT = '''## 프롬프트

당신은 유튜브 대본 작가입니다. 앞서 작성한 **대본 계획서**를 바탕으로 완성된 대본을 작성하세요.

### 작성 목표
- 청중의 욕구를 충족시키는 대본
- **최소 8,000자 이상** (필수!)
- 말하듯이 쓰기 (구어체)
- 비트 시퀀스에 따른 전개
- 글쓰기 기법 적용

### 🚨 금지 사항 (절대 포함하지 마세요!)
- **메타 설명 금지**: "다음은 요청하신 대로...", "아래는 대본입니다", "작성했습니다" 등
- **섹션 마커 금지**: "[본론 시작 - 비트1: 제목]", "[마무리]" 같은 대괄호 섹션 표시
- **구분선 금지**: "---", "===", "***" 같은 구분선
- **타임스탬프 금지**: "(01:45)", "(00:00)" 같은 시간 표시
- **작성 과정 설명 금지**: "~를 보강했습니다", "~를 추가했습니다" 등

**출력은 순수 대본 텍스트만!** 시청자가 바로 들을 수 있는 나레이션만 작성하세요.

---

## 대본 구조

### 전체 구성

| 파트 | 시간 | 분량 | 내용 |
|-----|------|------|------|
| 도입부 | 00:00~00:45 | 약 500자 | 핵심 미스터리/역설/충격 제시 |
| 전환부 | 00:45 | 1문장 | "구독과 좋아요는 영상 끝나고 누르셔도 괜찮습니다. 자 바로 시작할게요." |
| 본론 | 01:00~14:00 | 약 6,500자 | 비트 시퀀스에 따라 전개 |
| 마무리 | 14:00~15:00 | 약 800자 | 핵심 메시지 + 선택/행동 촉구 |
| 클로징 | 마지막 | 1문장 | "오늘 영상은 여기까지입니다. 다음 시간에 더 날카로운 이야기로 찾아뵙겠습니다." |

---

## 작성 프로세스

### STEP 1: 도입부 작성 (00:00~00:45)

**목표**: 45초 안에 청중을 붙잡는다

**필수 요소**:
- 핵심 미스터리 또는 역설 제시
- 충격적인 숫자 또는 사실
- "왜?"라는 궁금증 유발

**도입부 공식 (유형별)**:

| 유형 | 도입 공식 |
|-----|----------|
| 역설형 | "[상식]이면 당연히 [예상 결과]여야 하죠. 그런데 지금 정반대 상황이 벌어지고 있습니다." |
| 몰락형 | "[과거 영광]을 누리던 [대상]이 지금 뭘 하고 있는지 아십니까? [처참한 현재]입니다." |
| 가정형 | "여러분 [가정 상황]이라면 어떻게 하시겠습니까?" |
| 충격형 | "[충격적인 숫자]입니다. 이게 얼마나 미친 숫자냐고요?" |
| 해외사례형 | "[날짜], [장소]에서 [충격적 사건]이 터졌습니다." |

**적용할 글쓰기 기법**:
- 역설 설정
- 수사적 질문
- 구체적 숫자와 시간
- 감정 언어 ("무너졌습니다", "증발했습니다")
- 시간 프레이밍 ("스마트폰이 나오기도 전인 17년 만에")

### STEP 2: 본론 작성 (01:00~14:00)

**목표**: 비트 시퀀스에 따라 정보를 전달하면서 몰입 유지

**비트별 작성 가이드**:

| 비트 | 작성 방법 | 분량 |
|-----|----------|------|
| 통념 제시 | "우리는 당연히 ~라고 생각합니다" | 100~150자 |
| 통념 파괴 | "그런데 실제로는 달랐습니다. 진짜는 ~입니다" | 200~300자 |
| 증거 제시 | 구체적 숫자 + 계산 과정 보여주기 | 300~400자 |
| 비유 설명 | "~라고 생각해보세요" + 일상적 대상에 비유 | 150~200자 |
| 가상 인물 | "가상 인물 A씨를 설정하겠습니다. A씨는 [설정]입니다" | 400~600자 |
| 시뮬레이션 | 시간순/단계순으로 전개 | 500~800자 |
| N가지 나열 | "첫째, ~입니다. 둘째, ~입니다." | 각 150~200자 |
| 사례 제시 | 구체적 날짜 + 장소 + 무슨 일 | 200~300자 |
| 비교 | A vs B 병렬 구조 | 300~400자 |

**적용할 글쓰기 기법**:

1. **청중 대신 질문 던지기**
   - "이게 무슨 말이냐고요?"
   - "왜냐고요?"
   - "그럼 어떻게 되냐고요?"

2. **전환 신호**
   - "자, 여기서 핵심 질문입니다."
   - "그럼 이제 구조를 까보겠습니다."
   - "여기서 뒤집기가 나옵니다."

3. **"~는요" 패턴**
   - "한국은요. 다 경상도에서 나와요."
   - "결과는요. 러시아가 발이 묶인 겁니다."
   - "문제는요. 배가 없다는 겁니다."

4. **계산 제시**
   - "핵심 계산을 해보겠습니다."
   - "이걸 돈으로 환산하면 얼마나 될까요?"

5. **확인/강조**
   - "보셨습니까?"
   - "이게 현실입니다."
   - "차이가 보이십니까?"

6. **직접 인용/대화**
   - "김과장의 말을 들어보시죠. '~'"
   - "그때 그는 이렇게 말했습니다. '~'"

### STEP 3: 마무리 작성 (14:00~15:00)

**목표**: 핵심 메시지 각인 + 행동/선택 촉구

**마무리 공식 (유형별)**:

| 마무리 유형 | 공식 |
|------------|------|
| 되돌릴 수 없음 | "다시 돌아갈까요? 안 돌아갑니다. [이유]" |
| 선택 촉구 | "[A]할 것인가? [B]할 것인가? 선택은 지금 이 순간부터 시작됩니다." |
| 미래 경고 | "[연도]년이 되면 [결과]가 됩니다. [경고 메시지]" |
| 실용 가이드 | "지금 당장 [행동]하세요. [구체적 방법]" |
| 케이스 비교 | "케이스1: [안 움직인 경우]. 케이스2: [움직인 경우]. 당신은 어느 쪽입니까?" |

**적용할 글쓰기 기법**:
- 양자택일 제시
- 핵심 메시지 선언
- 시간의 긴급성 강조
- 마지막 행동 촉구

---

## 스타일 가이드

### 문장 스타일

| 항목 | 규칙 | 예시 |
|-----|------|------|
| 문장 길이 | 짧게 (20자 내외) | "그런데 폭락은 오지 않았습니다." |
| 문체 | 구어체 + 반말/존댓말 혼용 | "왜냐고요? 이유가 있습니다." |
| 숫자 | 구체적으로 | "약 30%" (X) → "29.7%" (O) |
| 감정 언어 | 강한 동사 사용 | "하락했다" (X) → "무너졌다" (O) |

### 감정 언어 변환표

| 약한 표현 | 강한 표현 |
|----------|----------|
| 하락했습니다 | 무너졌습니다 |
| 위기입니다 | 비명을 지르고 있습니다 |
| 어려워졌습니다 | 무릎 꿇었습니다 |
| 사라졌습니다 | 증발했습니다 |
| 손해봤습니다 | 배신당했습니다 |
| 영향을 받았습니다 | 아비규환입니다 |

### 리듬 패턴

**질문-답변 연쇄**:
[질문] 왜 이렇게 됐을까요?
[답변] 이유는 간단합니다. [설명]
[새 질문] 그럼 앞으로는 어떻게 될까요?

**긴장-해소 반복**:
[긴장] 여기서 문제가 생깁니다.
[해소] 답은 의외로 간단했습니다.
[새 긴장] 그런데 더 큰 문제가 있습니다.

---

## 감정 흐름 설계

### 유형별 감정 곡선

| 유형 | 감정 흐름 |
|-----|----------|
| 사회현상 분석형 | 호기심 → 충격 → 이해 → 위기감 → 선택 촉구 |
| 기업/산업 분석형 | 충격 → 공감 → 분노 → 이해 → 불안 → 각성 |
| 국제/방산 분석형 | 호기심 → 긴장 → 몰입 → 분노(배신자) → 부러움(성공자) → 긴박감 |
| 브랜드 몰락형 | 충격 → 공감 → 분노 → 이해 → 선택 |
| 위기 시뮬레이션형 | 충격 → 공포 → 몰입 → 불안 → 대비 촉구 |

---

## 체크리스트

### 작성 전 체크
- [ ] 대본 계획서가 완성되어 있는가?
- [ ] 선택한 욕구가 명확한가?
- [ ] 비트 시퀀스가 결정되어 있는가?
- [ ] 가상 인물이 설정되어 있는가?
- [ ] 핵심 메시지가 정의되어 있는가?

### 도입부 체크
- [ ] 45초 안에 핵심 미스터리가 제시되는가?
- [ ] 충격적인 숫자나 사실이 있는가?
- [ ] "왜?"라는 궁금증이 유발되는가?

### 본론 체크
- [ ] 비트 시퀀스대로 전개되는가?
- [ ] 구체적 숫자와 계산 과정이 있는가?
- [ ] 비유가 적절히 사용되었는가?
- [ ] 질문-답변 리듬이 유지되는가?
- [ ] 가상 인물 스토리가 몰입감 있는가?
- [ ] 전환 신호가 적절히 사용되었는가?

### 마무리 체크
- [ ] 핵심 메시지가 명확히 전달되는가?
- [ ] 선택/행동 촉구가 있는가?
- [ ] 감정적 여운이 남는가?

### 전체 체크
- [ ] 분량이 **최소 8,000자 이상**인가?
- [ ] 구어체로 자연스럽게 읽히는가?
- [ ] 감정 흐름이 설계대로 진행되는가?
- [ ] 선택한 욕구가 충족되는가?

---

## 출력 형식

JSON 형식으로 출력하세요:
```json
{
  "title": "영상 제목 (30자 이내, 충격적/호기심 유발)",
  "hook": "첫 훅 문장",
  "content": "순수 대본 텍스트 8000자 이상"
}
```

### content 작성 규칙

**순수 나레이션만 작성!**
- 타임스탬프 없이
- 섹션 마커 없이
- 구분선 없이
- 시청자가 바로 들을 수 있는 문장만

**필수 포함 문장**:
- 전환부: "구독과 좋아요는 영상 끝나고 누르셔도 괜찮습니다. 자 바로 시작할게요."
- 클로징: "오늘 영상은 여기까지입니다. 다음 시간에 더 날카로운 이야기로 찾아뵙겠습니다."

## 출력 예시 (도입부만)

2024년 1월, 중국 상하이의 한 아파트에서 30대 남성이 발견됐습니다. 유서에는 이렇게 적혀 있었습니다. "레버리지 3배, 마진콜, 전 재산 증발." 그는 빚투로 2억을 벌었다가, 단 3일 만에 5억을 잃었습니다. 원금 3억에 빚 2억이 더해진 겁니다.

그런데 지금 한국에서 똑같은 일이 벌어지고 있습니다. 빚투 규모 29조. 역대 최대입니다. 코스닥 레버리지 ETF에 매일 수천억이 몰리고 있습니다. 5일 만에 60% 수익. 사람들은 환호합니다. "돈 복사다!"

그런데 질문 하나 드리겠습니다. 그 60%가 -60%로 바뀌는 데 며칠이나 걸릴까요? 답은 3일입니다. 레버리지는 양날의 검이 아닙니다. 한쪽만 날카로운 칼입니다. 그리고 그 칼날은 당신을 향해 있습니다.

구독과 좋아요는 영상 끝나고 누르셔도 괜찮습니다. 자 바로 시작할게요.'''

    def _build_system_prompt(self) -> str:
        """시스템 프롬프트 생성 (DB에서만 가져옴 - DEFAULT_PROMPT 사용 안함)"""
        # DB에서 프롬프트 가져오기
        db_prompt = self.get_prompt()
        if db_prompt:
            self.log(f'DB 프롬프트 사용: {len(db_prompt)}자')
            return db_prompt

        # DB에 프롬프트가 없으면 최소한의 프롬프트 사용 (DEFAULT_PROMPT의 고정 구조 사용 안함)
        self.log('DB에 프롬프트 없음! 최소 프롬프트 사용', 'warning')
        return '''당신은 유튜브 대본 작가입니다.

## 🚨 필수 규칙
1. **대본 계획**이 있으면 그 구조와 비트 시퀀스를 **그대로** 따르세요
2. **리서치 결과**의 수치/사례를 대본에 자연스럽게 녹여주세요
3. **최소 8,000자 이상** 작성
4. 순수 대본만 출력 (메타 설명, 섹션 마커, 타임스탬프 금지)

JSON 형식으로 title과 content를 출력하세요.'''

    def _build_user_prompt(self, research: dict) -> str:
        """사용자 프롬프트 생성"""
        # 인용구 포맷
        quotes_text = ""
        for q in research.get('quotes', []):
            if isinstance(q, dict):
                quote = q.get('quote', '')
                source = q.get('source', '')
                emotion = q.get('emotion', '')
                if quote:
                    quotes_text += f'- "{quote}"'
                    if source:
                        quotes_text += f' - {source}'
                    if emotion:
                        quotes_text += f' ({emotion})'
                    quotes_text += '\n'
            else:
                quotes_text += f'- {q}\n'

        # 숫자/통계 포맷
        numbers_text = ""
        for n in research.get('numbers', []):
            if isinstance(n, dict):
                number = n.get('number', '')
                context = n.get('context', '')
                impact = n.get('impact', '')
                if number:
                    numbers_text += f'- {number}'
                    if context:
                        numbers_text += f': {context}'
                    if impact:
                        numbers_text += f' → {impact}'
                    numbers_text += '\n'
            else:
                numbers_text += f'- {n}\n'

        # 인물 사례 포맷
        person_text = ""
        for p in research.get('person_stories', []):
            if isinstance(p, dict):
                name = p.get('name', '익명')
                past = p.get('past', '')
                present = p.get('present', '')
                quote = p.get('quote', '')
                if name:
                    person_text += f'### {name}\n'
                    if past:
                        person_text += f'과거: {past}\n'
                    if present:
                        person_text += f'현재: {present}\n'
                    if quote:
                        person_text += f'말: "{quote}"\n'
                    person_text += '\n'

        # 시간 변화 포맷
        time_text = ""
        tc = research.get('time_change', {})
        if tc:
            if tc.get('past'):
                past = tc['past']
                time_text += f"**과거 ({past.get('year', '')})**: {past.get('situation', '')}"
                if past.get('numbers'):
                    time_text += f" - {past.get('numbers')}"
                time_text += '\n'
            if tc.get('turning_point'):
                tp = tc['turning_point']
                time_text += f"**전환점 ({tp.get('year', '')})**: {tp.get('event', '')} → {tp.get('impact', '')}\n"
            if tc.get('present'):
                present = tc['present']
                time_text += f"**현재 ({present.get('year', '')})**: {present.get('situation', '')}"
                if present.get('numbers'):
                    time_text += f" - {present.get('numbers')}"
                time_text += '\n'

        # 역설 포맷
        paradox_text = ""
        paradox = research.get('paradox', {})
        if paradox:
            if paradox.get('common_belief'):
                paradox_text += f"**통념**: {paradox['common_belief']}\n"
            if paradox.get('reality'):
                paradox_text += f"**현실**: {paradox['reality']}\n"
            if paradox.get('insight'):
                paradox_text += f"**통찰**: {paradox['insight']}\n"

        # 시청자 연결 포맷
        viewer_text = ""
        vc = research.get('viewer_connection', {})
        if vc:
            if vc.get('direct_impact'):
                viewer_text += f"직접 영향: {vc['direct_impact']}\n"
            if vc.get('self_check'):
                viewer_text += f"자가 점검: {vc['self_check']}\n"

        # 기사별 요약 포맷
        article_text = ""
        for i, article in enumerate(research.get('article_summaries', [])[:5], 1):  # 최대 5개
            query = article.get('query', '')
            summary = article.get('summary', '')
            if summary:
                article_text += f"\n### 검색 {i}: {query}\n"
                article_text += summary[:1500] + ("..." if len(summary) > 1500 else "") + "\n"

        # 제목 정보
        best_title = research.get('best_title', {})
        title_info = ""
        if best_title:
            if best_title.get('title'):
                title_info += f"선정 제목: {best_title['title']}\n"
            if best_title.get('hook'):
                title_info += f"훅: {best_title['hook']}\n"
            if best_title.get('pattern'):
                title_info += f"패턴: {best_title['pattern']}\n"

        # 수동 추가 자료 포맷
        manual_notes_text = research.get('manual_notes', '')

        # content_analysis (YouTube 분석 결과)
        content_analysis_text = ""
        script_plan_text = ""
        research_result_text = ""

        if research.get('content_analysis'):
            ca = research['content_analysis']

            # 대본 계획 (script_plan) - 가장 중요!
            if ca.get('script_plan'):
                script_plan_text = ca['script_plan']

            # 리서치 결과 (research_result)
            if ca.get('research_result'):
                research_result_text = ca['research_result']

            # 기존 분석 데이터
            if ca.get('summary') or ca.get('key_topics') or ca.get('viewer_interests'):
                content_analysis_text = f"""
### 영상 요약
{ca.get('summary', '')}

### 핵심 주제
{', '.join(ca.get('key_topics', []))}

### 시청자 관심사
{chr(10).join('- ' + i for i in ca.get('viewer_interests', []))}
"""
            # draft_outline
            draft = ca.get('draft_outline', {})
            if draft:
                content_analysis_text += f"""
### 대본 뼈대 (참고)
**훅:** {draft.get('hook', '')}
**인트로:** {draft.get('intro', '')}
**본론 포인트:**
{chr(10).join('- ' + p for p in draft.get('body_points', []))}
**결론:** {draft.get('conclusion', '')}
"""

        # 주요 댓글
        highlight_comments_text = ""
        if research.get('highlight_comments'):
            highlight_comments_text = chr(10).join(f'- "{c}"' for c in research['highlight_comments'][:10])

        # 원본 자막 (참고용)
        transcript_text = ""
        if research.get('transcript'):
            transcript_text = research['transcript'][:3000] + "..." if len(research.get('transcript', '')) > 3000 else research.get('transcript', '')

        return f"""# 대본 작성 자료

## 주제
{research.get('topic', '주제 없음')}

## 🎯 대본 계획 (핵심! 이 구조대로 작성)
{script_plan_text if script_plan_text else '(없음)'}

## 📚 리서치 결과 (대본 작성에 활용)
{research_result_text if research_result_text else '(없음)'}

## 수동 추가 자료
{manual_notes_text if manual_notes_text else '(없음)'}

## 제목 정보
{title_info}

## YouTube 콘텐츠 분석 (참고)
{content_analysis_text if content_analysis_text else '(없음)'}

## 주요 댓글 (시청자 관심사)
{highlight_comments_text if highlight_comments_text else '(없음)'}

## 원본 영상 자막 (어투/표현 참고)
{transcript_text if transcript_text else '(없음)'}

## 인용구 (대본에 반드시 활용)
{quotes_text if quotes_text else '(없음)'}

## 숫자/통계 (대본에 반드시 활용)
{numbers_text if numbers_text else '(없음)'}

## 인물 사례 (대본에 반드시 활용)
{person_text if person_text else '(없음)'}

## 시간 변화 스토리
{time_text if time_text else '(없음)'}

## 역설/반전 요소
{paradox_text if paradox_text else '(없음)'}

## 시청자 연결
{viewer_text if viewer_text else '(없음)'}

## 검색 결과 상세 (참고 자료)
{article_text if article_text else '(없음)'}

---

위 자료를 바탕으로 **최소 8000자 이상**의 유튜브 대본을 작성해주세요.

**🚨 핵심:**
1. **대본 계획**의 구조와 비트 시퀀스를 그대로 따르세요
2. **리서치 결과**의 수치/사례를 대본에 자연스럽게 녹여주세요
3. 순수 대본만 작성하세요 (화면 지시, 씬 번호 없음)

JSON 형식으로 출력하세요."""

    def _clean_content(self, content: str) -> str:
        """대본에서 메타 텍스트 제거 + JSON이면 content 추출"""
        if not content:
            return content

        # JSON 형식이면 content 필드만 추출
        if '```json' in content or ('"content"' in content and '"title"' in content):
            try:
                # ```json ... ``` 블록 추출
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                    content = data.get('content', content)
                else:
                    # 직접 JSON 파싱
                    data = json.loads(content)
                    content = data.get('content', content)
            except (json.JSONDecodeError, Exception):
                pass  # 파싱 실패하면 원본 사용

        lines = content.split('\n')
        cleaned_lines = []

        # 메타 설명 패턴 (첫 몇 줄에서만 제거)
        meta_patterns = [
            r'^제시해주신',
            r'^요청하신',
            r'^다음은.*대본',
            r'^아래는.*대본',
            r'^작성했습니다',
            r'^.*보강.*대본',
            r'^.*기존.*뼈대',
        ]

        # 섹션 마커 패턴
        section_patterns = [
            r'^\*\*\(.*?\)\*\*$',  # **(도입부)**
            r'^\*\*\[.*?\]\*\*$',  # **[본론]**
            r'^\[.*?\]$',          # [도입부]
            r'^###\s*\[.*?\]',     # ### [대본]
            r'^##\s*\[.*?\]',      # ## [대본]
            r'^\(\d{1,2}:\d{2}\)',  # (01:45) 타임스탬프만 있는 줄
        ]

        skip_first_meta = True
        for i, line in enumerate(lines):
            stripped = line.strip()

            # 빈 줄은 유지
            if not stripped:
                cleaned_lines.append(line)
                continue

            # 구분선 제거
            if stripped in ['---', '===', '***', '---\n']:
                continue

            # 첫 몇 줄에서 메타 설명 제거
            if skip_first_meta and i < 10:
                is_meta = False
                for pattern in meta_patterns:
                    if re.match(pattern, stripped):
                        is_meta = True
                        break
                if is_meta:
                    continue
                # 실제 대본 시작하면 메타 스킵 종료
                if len(stripped) > 30:
                    skip_first_meta = False

            # 섹션 마커 제거
            is_section_marker = False
            for pattern in section_patterns:
                if re.match(pattern, stripped):
                    is_section_marker = True
                    break
            if is_section_marker:
                continue

            # 타임스탬프 제거 (줄 시작 부분만)
            line = re.sub(r'^\s*\(\d{1,2}:\d{2}\)\s*', '', line)

            # 인라인 섹션 마커 제거
            line = re.sub(r'\s*\[본론[^\]]*\]\s*', ' ', line)
            line = re.sub(r'\s*\[도입부[^\]]*\]\s*', ' ', line)
            line = re.sub(r'\s*\[마무리[^\]]*\]\s*', ' ', line)
            line = re.sub(r'\s*\[비트\d+[^\]]*\]\s*', ' ', line)

            # 마크다운 헤더 제거
            line = re.sub(r'^#+\s*', '', line)

            # 정리
            line = line.strip()
            if line:
                cleaned_lines.append(line)

        # 결과 조합 (연속 빈줄 정리)
        result = '\n'.join(cleaned_lines)
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()

    def _parse_response(self, response: str, research: dict) -> dict:
        """응답 파싱"""
        # JSON 추출 시도
        try:
            # ```json ... ``` 블록 찾기
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                content = self._clean_content(data.get('content', ''))
                return {
                    'title': data.get('title', research.get('topic', '제목 없음')),
                    'content': content,
                }

            # JSON 객체 직접 찾기
            json_match = re.search(r'\{[^{}]*"content"[^{}]*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                content = self._clean_content(data.get('content', ''))
                return {
                    'title': data.get('title', research.get('topic', '제목 없음')),
                    'content': content,
                }
        except json.JSONDecodeError:
            pass

        # JSON 파싱 실패 시 전체를 대본으로 사용
        # 제목 추출 시도
        title = research.get('best_title', {}).get('title', '') or research.get('topic', '제목 없음')

        # 첫 줄이 제목인지 확인
        lines = response.strip().split('\n')
        if lines and len(lines[0]) < 80 and not lines[0].startswith(('만약', '여러분', '혹시', '"')):
            potential_title = lines[0].strip().replace('#', '').replace('*', '').strip()
            if potential_title:
                title = potential_title
                response = '\n'.join(lines[1:]).strip()

        content = self._clean_content(response)
        return {
            'title': title,
            'content': content,
        }

    def _expand_content(self, content: str, needed_chars: int, attempt: int = 1, research_summary: str = '') -> str:
        """글자수 보강"""
        original_len = len(content)

        # 사용자 커스텀 프롬프트 가져오기
        system_prompt = self._build_system_prompt()

        # 시도 횟수에 따라 다른 전략 사용
        if attempt == 1:
            strategy = "구체적 사례와 비유 추가"
            extra_instruction = """- 추상적인 설명을 구체적 사례로 확장
- 숫자는 일상 물건으로 환산 (치킨, 아이폰, 월급 등)
- "예를 들어", "실제로" 문구 사용"""
        elif attempt == 2:
            strategy = "감정과 질문 추가"
            extra_instruction = """- 시청자 공감 질문 추가 ("혹시 여러분도...?")
- 감정 유도 문장 ("솔직히 화가 나지 않으세요?")
- 긴장감 조성 ("근데요, 여기서 끝이 아닙니다")"""
        else:
            strategy = "전문가 인용과 미래 전망 추가"
            extra_instruction = """- 전문가 의견 추가 ("~전문가는 이렇게 말합니다")
- 미래 시나리오 ("이대로 가면 10년 후...")
- 대안과 해결책 확장"""

        self.log(f'보강 전략 {attempt}: {strategy}')
        self.log(f'보강에 사용할 시스템 프롬프트: {len(system_prompt)}자')
        self.log(f'보강 프롬프트 생성 중... (기존 대본 {original_len}자)')

        # 리서치 컨텍스트 포함 (전체)
        research_context = ""
        if research_summary:
            research_context = f"""
**원본 리서치 (이 내용을 기반으로 보강하세요):**
{research_summary}

---
"""

        # 보강 위치 지정 (본문 중간중간에 삽입하도록)
        expand_prompt = f"""현재 대본이 {original_len}자입니다. 8000자가 되도록 {needed_chars}자 이상 추가해야 합니다.
{research_context}
**중요: 반드시 {needed_chars}자 이상 추가해주세요!**

**이번 보강 전략: {strategy}**
{extra_instruction}

**보강 위치:**
1. 도입부 (500자 이상)
2. 본론 각 파트 (200-300자씩)
3. 전환부마다 (100자씩)
4. 결론 (300자 이상)

**필수:**
- 대본 전체를 다시 작성하세요
- 기존 내용 생략 없이 확장만 하세요
- 리서치 내용과 관련된 추가 설명/사례 포함
- 최종 글자수가 8000자 이상이어야 합니다

**🚨 금지 (절대 포함 금지!):**
- "네, 알겠습니다", "요청하신 대로", "보강하겠습니다" 등 메타 설명
- "---", "===" 구분선
- "[도입부]", "**(본론)**", "### 제목" 등 섹션 마커
- "(00:00)" 타임스탬프
- 순수 나레이션만 출력!

기존 대본 ({original_len}자):
---
{content}
---

위 대본을 보강하여 8000자 이상의 전체 대본을 출력하세요 (순수 나레이션만!):"""

        # 커스텀 프롬프트 + 보강 프롬프트
        full_prompt = f"{system_prompt}\n\n---\n\n{expand_prompt}"
        result = self.call_gemini(full_prompt)
        result = self._clean_content(result)  # 메타 텍스트 제거
        new_len = len(result)

        self.log(f'Gemini 결과: {new_len}자 (원본: {original_len}자)')

        # 보강 결과가 오히려 짧아졌으면 원본 유지
        if new_len < original_len:
            self.log(f'⚠️ 보강 실패: {original_len}자 → {new_len}자 (원본 유지!)', 'error')
            return content

        # 차이가 500자 이상 줄었으면 이상한 응답 - 원본 유지
        if new_len < original_len - 500:
            self.log(f'⚠️ 이상한 응답 감지: {original_len}자 → {new_len}자 (원본 유지!)', 'error')
            return content

        # 보강 결과가 거의 안 늘었으면 재시도용으로 직접 추가
        if new_len < original_len + 300:
            self.log(f'⚠️ 보강 미미: +{new_len - original_len}자, 추가 문단 생성...', 'warning')
            # 추가 문단 생성
            extra_prompt = f"""다음 주제로 500자 분량의 추가 문단을 작성해주세요:

주제: 시청자가 직접 체감할 수 있는 일상 속 영향

조건:
- "여러분의 장바구니를 한번 떠올려보세요..." 로 시작
- 구체적인 가격 비교 (작년 vs 올해)
- "~잖아요/~거든요" 어미 사용
- 500자 이상

추가 문단만 출력 (메타 설명 없이 순수 나레이션만!):"""
            extra = self.call_gemini(extra_prompt)
            extra = self._clean_content(extra)
            self.log(f'추가 문단: {len(extra)}자 생성됨')
            if len(extra) > 200:
                # 결론 앞에 삽입 (rfind는 못찾으면 -1 반환, -1은 truthy라서 or 체이닝 안됨)
                insert_point = result.rfind('결론')
                if insert_point == -1:
                    insert_point = result.rfind('마지막')
                if insert_point == -1:
                    insert_point = result.rfind('정리하면')
                if insert_point == -1:
                    # 못 찾으면 끝에서 500자 앞에 삽입
                    insert_point = max(len(result) - 500, len(result) // 2)

                result = result[:insert_point] + "\n\n" + extra + "\n\n" + result[insert_point:]
                self.log(f'삽입 완료: {len(result)}자')
            else:
                self.log(f'추가 문단 너무 짧음: {len(extra)}자', 'warning')

        return result

    def _compress_content(self, content: str, excess_chars: int) -> str:
        """글자수 압축"""
        compress_prompt = f"""아래 대본이 {excess_chars}자 초과입니다. 8000자 내외로 압축해주세요.

**압축 방법:**
- 중복되는 설명 제거
- 핵심만 남기고 부연 설명 축소
- 흐름은 유지

기존 대본:
{content}

압축된 전체 대본을 출력해주세요 (JSON 없이 순수 텍스트만):"""

        return self.call_gemini(compress_prompt)
