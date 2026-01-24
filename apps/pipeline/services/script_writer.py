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

        # 글자수 확인 및 보강 (최대 3회 시도)
        char_count = len(draft_data['content'])
        self.log(f'초기 대본 글자수: {char_count}자')

        retry_count = 0
        max_retries = 3

        while char_count < 7500 and retry_count < max_retries:
            retry_count += 1
            needed = 8000 - char_count
            self.update_progress(60 + (retry_count * 10), f'글자수 부족 ({char_count}자), 보강 중... ({retry_count}/{max_retries})')
            self.log(f'보강 시도 {retry_count}: {char_count}자 → +{needed}자 필요')

            draft_data['content'] = self._expand_content(draft_data['content'], needed)
            char_count = len(draft_data['content'])
            self.log(f'보강 후: {char_count}자')

        if char_count < 7500:
            self.log(f'⚠️ {max_retries}회 보강 후에도 미달: {char_count}자', 'error')

        if char_count > 8500:
            self.update_progress(85, f'글자수 초과 ({char_count}자), 압축 중...')
            draft_data['content'] = self._compress_content(draft_data['content'], char_count - 8000)
            char_count = len(draft_data['content'])

        # DB에 저장
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

    def _build_system_prompt(self) -> str:
        """시스템 프롬프트 생성 (DB에서 불러옴)"""
        # DB에서 프롬프트 가져오기
        db_prompt = self.get_prompt()
        if db_prompt:
            return db_prompt

        # DB에 프롬프트가 없으면 에러 (관리자에서 설정 필요)
        raise ValueError(
            '대본 작성 프롬프트가 설정되지 않았습니다. '
            '관리자 페이지에서 script_writer 프롬프트를 추가하거나, '
            'python manage.py load_prompts 명령을 실행해주세요.'
        )

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

    def _expand_content(self, content: str, needed_chars: int) -> str:
        """글자수 보강"""
        expand_prompt = f"""아래 대본의 글자수가 부족합니다. {needed_chars}자 이상 추가해주세요.

**보강 방법:**
1. 비유와 예시를 더 풍부하게
2. "상황이 보이시나요?" 같은 긴장 유도 문장 추가
3. 숫자를 일상 물건으로 환산하는 설명 추가
4. 인물 사례를 더 상세하게

**주의:**
- 기존 흐름과 자연스럽게 연결
- 새로운 내용이 아닌 기존 내용의 심화
- ~잖아요/~거든요 어미 패턴 유지

기존 대본:
{content}

보강된 전체 대본을 출력해주세요 (JSON 없이 순수 텍스트만):"""

        return self.call_gemini(expand_prompt)

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
