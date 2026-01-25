import json
import re
from .base import BaseStepService
from apps.pipeline.models import Draft


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

        # 시스템 프롬프트 (대본 작성 가이드)
        system_prompt = self._build_system_prompt()

        # 사용자 프롬프트 (리서치 자료 포함)
        user_prompt = self._build_user_prompt(research_data)

        # Gemini 호출
        self.update_progress(20, 'AI 대본 생성 중...')
        self.log('대본 생성 시작')

        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        response = self.call_gemini(full_prompt)

        # 응답에서 대본 추출
        self.update_progress(60, '대본 정제 중...')
        draft_data = self._parse_response(response, research_data)

        # 글자수 확인 및 보강 (1회만 시도)
        char_count = len(draft_data['content'])
        self.log(f'초기 대본 글자수: {char_count}자')

        # 가장 긴 버전 = 초기 버전
        best_content = draft_data['content']
        best_count = char_count

        # 7500자 미만이면 1회 보강 시도
        if char_count < 7500:
            needed = 8000 - char_count
            self.update_progress(70, f'글자수 부족 ({char_count}자), 보강 중...')
            self.log(f'보강 시도: {char_count}자 → +{needed}자 필요')

            try:
                # 리서치 전체 데이터
                research_text = f"""주제: {research_data.get('topic', '')}
요약: {research_data.get('summary', '')}
인용구: {research_data.get('quotes', [])}
숫자/통계: {research_data.get('numbers', [])}
인물 사례: {research_data.get('person_stories', [])}"""

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

        if char_count < 7500:
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

        if not hasattr(self.project, 'research') or not self.project.research:
            return None

        r = self.project.research
        return {
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

    # 기본 프롬프트 (DB에 없을 때 사용)
    DEFAULT_PROMPT = '''# 경제 유튜브 대본 작가

당신은 시청자가 끝까지 보게 만드는 경제 유튜브 대본을 작성합니다.

## 절대 규칙

1. **글자수: 정확히 8000자** (7500~8500자 범위, 공백 포함)
2. **순수 대본만**: [화면: ...] 같은 지시 금지, 씬 분할 금지
3. **문체**: 말하는 느낌, ~잖아요/~거든요/~죠? 패턴 사용

## 금지 사항
- "~에 대해 알아보겠습니다" 금지
- "안녕하세요, 오늘은..." 금지
- 뉴스 앵커 톤 금지
- 전문 용어 설명 없이 사용 금지

## 대본 구조 (15분, 8000자)

### PART 1: 오프닝 [약 800자]

**1-1. 상황 몰입 오프닝**
공식: "여러분이 [구체적 상황]에 있다고 가정해볼게요. 그런데 [변화]가 일어납니다. 듣기엔 [긍정적]이죠? 그런데 여기 함정이 있습니다."

**1-2. 로드맵 제시**
"오늘 이 문제에 대해 공부해볼 겁니다.
첫 번째, [개념]이 뭔지
두 번째, 왜 [강한 부정 표현]인지
마지막으로 생존 포인트를 짚어드리겠습니다."

### PART 2: 개념 설명 [약 1200자]

**비유 기반 설명**
- 어려운 용어 → 일상 비유 (쇼핑몰, 택배, 게임 등)
- "떠올려 보세요" → "이게 바로 ~예요" 패턴

**배경 맥락**
- 구체적 연도/숫자 필수
- "하지만 진짜 문제는 이제부터입니다"로 전환

### PART 3: 본질 분석 [약 1200자]

**본질 파헤치기**
- "왜 ~일까요?" 자문자답
- 이해관계자 입장에서 설명
- "그런데 ~가 막아버리면 어떻게 될까요?"

**숫자 체감화**
- 금액 → 일상 환산 (치킨 N마리, 아이폰 N대)
- "이 돈이면 뭘 할 수 있을까요?"

### PART 4: 문제점 심화 [약 2400자]

**문제점 3개 나열**
각 문제마다:
- "[N]번째 문제, [문제명]입니다"
- 비유 + 데이터 + 결론
- "이건 [A]를 막으려다 [B]를 유발하는 독약이에요"

**Before/After 비교**
- 구체적 숫자로 변화 전후 비교
- 피해 금액 계산

### PART 5: 액션 플랜 [약 1600자]

"그럼 지금 당장 뭘 체크해야 할까요?"

**첫 번째**: [구체적 행동] - 숫자 기준 제시
**두 번째**: [구체적 행동] - 도구/방법 제시
**세 번째**: [정보 행동] - 경고 문장 포함

"망설이는 순간 선택지가 사라집니다"

### PART 6: 마무리 [약 800자]

- 핵심 정리 (속담 활용)
- 액션 요약
- "여러분의 [보호대상]은 여러분이 지켜야 합니다"
- 구독/좋아요 요청

## 필수 어미 패턴

| 용도 | 어미 | 예시 |
|-----|------|------|
| 동의 유도 | ~잖아요 | "돈을 바로 안 보내잖아요" |
| 설명 | ~거든요 | "피해자가 35,000명이거든요" |
| 확인 | ~죠? | "숫자로 보면 심각하죠?" |
| 긴장 | ~될까요? | "어떻게 될까요?" |
| 가정 | ~칩시다 | "3억짜리 집이 있다고 칩시다" |

## 긴장 유도 문장 (90초=약 500자마다 1개)

- "상황이 보이시나요?"
- "감 오시나요?"
- "이게 뭘 의미하는지 아세요?"
- "더 무서운 건 이제부터입니다"
- "구체적인 숫자로 보여드릴게요"

## 금액 환산 기준

- 50만원: 치킨 25마리
- 200만원: 아이폰 1대
- 2000만원: 중형차, 해외여행 8번
- 1억원: 지방 소형 아파트
- 2억원: 수도권 외곽 아파트

## 출력 형식

JSON 형식으로 출력하세요:
```json
{
  "title": "영상 제목 (30자 이내, 충격적/호기심 유발)",
  "hook": "첫 훅 문장",
  "content": "순수 대본 텍스트 8000자..."
}
```'''

    def _build_system_prompt(self) -> str:
        """시스템 프롬프트 생성 (DB에서 불러오거나 기본값 사용)"""
        # DB에서 프롬프트 가져오기
        db_prompt = self.get_prompt()
        if db_prompt:
            return db_prompt

        # DB에 프롬프트가 없으면 기본 프롬프트 사용
        self.log('DB에 프롬프트 없음, 기본 프롬프트 사용', 'warning')
        return self.DEFAULT_PROMPT

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

        return f"""# 리서치 자료

## 주제
{research.get('topic', '주제 없음')}

## 요약
{research.get('summary', '')}

## 제목 정보
{title_info}

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

위 리서치 자료를 바탕으로 8000자 분량의 경제 유튜브 대본을 작성해주세요.

**필수 사항:**
1. 위 인용구, 숫자, 인물 사례를 대본에 자연스럽게 녹여주세요
2. "상황 가정 → 동의 유도 → 반전" 공식으로 시작하세요
3. 금액은 일상 물건으로 환산하세요 (치킨, 아이폰 등)
4. 90초(약 500자)마다 긴장 유도 문장을 넣으세요
5. 순수 대본만 작성하세요 (화면 지시, 씬 번호 없음)

**대본 구조 (필수!):**
1. **오프닝 훅** (상황 가정 → 반전)
2. **로드맵 제시** ("오늘 다룰 내용: 첫째 ~, 둘째 ~, 마지막으로 ~")
3. **구독/좋아요 요청** ← 로드맵 직후, 본론 들어가기 전에 반드시 삽입!
   - 예시: "영상 시작하기 전에, 이런 경제 콘텐츠 유익하셨다면 구독과 좋아요 한번 부탁드릴게요. 알림 설정까지 해두시면 새 영상 바로 받아보실 수 있습니다. 자, 그럼 본론으로 들어가 볼게요."
   - 자연스럽게 1-2문장으로 삽입
4. **본론** (개념 설명 → 문제점 분석 → 심화)
5. **액션 플랜** (3가지 행동 지침)
6. **마무리**

JSON 형식으로 출력하세요."""

    def _parse_response(self, response: str, research: dict) -> dict:
        """응답 파싱"""
        # JSON 추출 시도
        try:
            # ```json ... ``` 블록 찾기
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                return {
                    'title': data.get('title', research.get('topic', '제목 없음')),
                    'content': data.get('content', ''),
                }

            # JSON 객체 직접 찾기
            json_match = re.search(r'\{[^{}]*"content"[^{}]*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    'title': data.get('title', research.get('topic', '제목 없음')),
                    'content': data.get('content', ''),
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

        return {
            'title': title,
            'content': response,
        }

    def _expand_content(self, content: str, needed_chars: int, attempt: int = 1, research_summary: str = '') -> str:
        """글자수 보강"""
        original_len = len(content)

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

기존 대본 ({original_len}자):
---
{content}
---

위 대본을 보강하여 8000자 이상의 전체 대본을 출력하세요:"""

        result = self.call_gemini(expand_prompt)
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

추가 문단만 출력:"""
            extra = self.call_gemini(extra_prompt)
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
