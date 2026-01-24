import json
import re
import time
from google.genai import types
from .base import BaseStepService
from apps.pipeline.models import Research


# 에이전트 시스템 프롬프트
RESEARCHER_SYSTEM_PROMPT = """# 리서처 (자료 수집 전문가)

당신은 **터지는 영상을 만들기 위한 재료**를 수집하는 전문가입니다.

## 핵심 원칙

```
단순 정보 수집 ❌
시청자 감정을 건드리고 호기심을 자극하는 재료 ✅
```

## search_web 도구 사용법

필요한 정보가 부족하면 search_web 도구를 호출하세요.
한 번에 모든 정보가 나오지 않으면, 다른 검색어로 여러 번 검색하세요.

### 검색 키워드 예시
- "[주제] 2025 통계"
- "[주제] 폐업률 역대"
- "[주제] 인터뷰 한탄"
- "[주제] 사장 '버틸 수가 없다'"
- "[주제] 전문가 전망"
- "[주제] 2010년 vs 2025년"

## 찾아야 할 것

### 1. 충격적 인용구 (3개 이상 필수!)
- "30년 일했는데 하루아침에 쓸모없어졌어요"
- "월 500 벌다가 지금은 50도 안 돼요"
- 검색어: "[주제] 인터뷰", "[주제] 사장 발언", "[주제] 한탄"

### 2. 충격적 숫자/통계 (10개 이상!)
- "87년 역사에서 처음으로"
- "15년 만에 15배"
- 검색어: "[주제] 통계 2025", "[주제] 역대 최초", "[주제] 몇 년 만에"

### 3. 시간 변화 스토리 (과거 vs 현재)
- [과거 숫자] → [변화 시점] → [현재 숫자]
- 검색어: "[주제] 2010년 vs 2025년", "[주제] 황금기 몰락"

### 4. 인물 스토리 (2개 이상!)
- "30년 경력 정비사 김철수 씨(55)"
- 검색어: "[주제] 사장 인터뷰", "[업종] 경력 폐업"

### 5. 역설/반전 요소
- "열심히 안 해서 망한게 아니에요. 성실한 업종부터 무너져요."
- 검색어: "[주제] 역설", "[주제] 의외"

### 6. 시청자 연결
- "5년 뒤 여러분이 차 살 때..."
- "이 산업 망하면 일자리 100만개 사라져요"

## 필수 체크리스트 (충족될 때까지 검색!)

- [ ] 인용구 3개 이상
- [ ] 숫자 10개 이상
- [ ] 시간 변화 (과거→현재)
- [ ] 인물 사례 2개 이상
- [ ] 역설 요소 1개 이상
- [ ] 시청자 연결

## 완료 조건

위 체크리스트가 모두 충족되면, 아래 JSON 형식으로 최종 결과를 출력하세요.
부족한 항목이 있으면 추가 검색을 해주세요.

```json
{
    "topic": "주제",
    "title_candidates": ["제목1", "제목2", "제목3", "제목4", "제목5"],
    "best_title": {
        "title": "가장 좋은 제목",
        "pattern": "인용구형/숫자형/질문형",
        "hook": "영상 시작 첫 문장"
    },
    "quotes": [
        {"quote": "인용문", "source": "누구", "emotion": "한탄/충격/분노", "usable_for": "인트로/본문"}
    ],
    "numbers": [
        {"number": "숫자", "context": "맥락", "impact": "충격 포인트"}
    ],
    "time_change": {
        "past": {"year": "시점", "situation": "상황", "numbers": "수치"},
        "turning_point": {"year": "시점", "event": "사건", "impact": "영향"},
        "present": {"year": "시점", "situation": "상황", "numbers": "수치"}
    },
    "person_stories": [
        {"name": "이름", "age": "나이", "career": "경력", "past": "과거", "present": "현재", "quote": "발언", "emotion": "감정"}
    ],
    "paradox": {
        "common_belief": "일반적 믿음",
        "reality": "실제 현실",
        "insight": "통찰"
    },
    "viewer_connection": {
        "direct_impact": "직접적 영향",
        "indirect_impact": "간접적 영향",
        "self_check": "시청자에게 던지는 질문"
    },
    "narrative_structure": {
        "intro": {"hook": "첫 문장", "setup": "상황 설정"},
        "act1": {"title": "1막", "points": ["포인트1", "포인트2"]},
        "act2": {"title": "2막", "points": ["포인트1", "포인트2"]},
        "act3": {"title": "3막", "points": ["포인트1", "포인트2"]},
        "conclusion": {"summary": "핵심 메시지", "cta": "행동 유도"}
    },
    "sources": [
        {"title": "기사 제목", "url": "URL", "publisher": "출처", "key_info": "핵심 정보"}
    ]
}
```

JSON만 출력하세요. 다른 텍스트 없이."""


class ResearcherService(BaseStepService):
    """리서치 에이전트 - Function Calling으로 자율 검색

    특징:
    - Gemini가 필요할 때 search_web 도구 호출
    - 중간 저장으로 이어하기 지원
    - 오류 시 30초 대기 후 3회 재시도
    """

    agent_name = 'researcher'

    # 재시도 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 30  # 초

    def __init__(self, execution):
        super().__init__(execution)
        self._search_count = 0
        self._all_sources = []

    def execute(self):
        self.update_progress(5, '리서치 에이전트 시작...')

        # 입력 확인 (Topic 모델 또는 수동 입력)
        manual_input = self.get_manual_input()

        topic_title = None
        source_url = ''

        if manual_input:
            topic_title = manual_input.strip()
        elif hasattr(self.project, 'topic') and self.project.topic:
            topic_title = self.project.topic.title
            source_url = self.project.topic.url or ''

        if not topic_title:
            raise ValueError('리서치할 주제를 입력해주세요.')

        # 중간 데이터 복원
        self._restore_intermediate_data()

        self.log(f'주제: {topic_title}', 'info')
        if self._search_count > 0:
            self.log(f'이전 검색 {self._search_count}개 복원됨', 'info')

        self.update_progress(10, f'"{topic_title}" 리서치 중...')

        # 에이전트 실행
        result = self._run_agent(topic_title)

        # 결과 검증 - 필수 항목 체크
        if not result.get('quotes') and not result.get('numbers'):
            raise ValueError(
                f'리서치 결과가 불충분합니다. '
                f'검색 {self._search_count}회 수행했으나 인용구/숫자 데이터가 없습니다. '
                f'다시 실행해주세요.'
            )

        # DB에 저장
        self.update_progress(95, '결과 저장 중...')
        self._save_research(topic_title, source_url, result)

        # 중간 데이터 정리 (완료됐으므로)
        self._clear_intermediate_data()

        # 결과 요약 로그
        self.log(
            f'리서치 완료: 인용구 {len(result.get("quotes", []))}개, '
            f'숫자 {len(result.get("numbers", []))}개, '
            f'인물 {len(result.get("person_stories", []))}개',
            'result'
        )

        self.update_progress(100, f'리서치 완료 (검색 {self._search_count}회)')

    def _restore_intermediate_data(self):
        """중간 저장 데이터 복원"""
        data = self.execution.intermediate_data or {}

        if data.get('searches'):
            self._search_count = len(data['searches'])
            for search in data['searches']:
                self._all_sources.extend(search.get('sources', []))

    def _save_intermediate_data(self, query: str, text: str, sources: list):
        """검색 결과 중간 저장"""
        data = self.execution.intermediate_data or {}

        if 'searches' not in data:
            data['searches'] = []

        data['searches'].append({
            'query': query,
            'text': text[:2000],  # 텍스트는 2000자로 제한
            'sources': sources
        })

        self.execution.intermediate_data = data
        self.execution.save(update_fields=['intermediate_data'])

    def _clear_intermediate_data(self):
        """중간 데이터 정리"""
        self.execution.intermediate_data = {}
        self.execution.save(update_fields=['intermediate_data'])

    def _get_previous_context(self) -> str:
        """이전 검색 결과를 컨텍스트로 변환"""
        data = self.execution.intermediate_data or {}
        searches = data.get('searches', [])

        if not searches:
            return ""

        context = "\n\n## 이전에 검색한 내용:\n"
        for i, search in enumerate(searches, 1):
            context += f"\n### 검색 {i}: {search['query']}\n"
            context += search.get('text', '')[:500] + "...\n"

        return context

    def _search_web_with_retry(self, query: str) -> str:
        """웹 검색 (재시도 포함)"""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return self._search_web(query)
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    wait_time = self.RETRY_DELAY * (attempt + 1)
                    self.log(
                        f'검색 실패 (시도 {attempt + 1}/{self.MAX_RETRIES}): {str(e)}. '
                        f'{wait_time}초 후 재시도...',
                        'error'
                    )
                    time.sleep(wait_time)

        # 모든 재시도 실패
        self.log(f'검색 최종 실패: {str(last_error)}', 'error')
        return f"검색 실패 (3회 재시도 후): {str(last_error)}"

    def _search_web(self, query: str) -> str:
        """웹 검색 도구 - Gemini가 호출함

        Args:
            query: 검색할 내용 (예: "자영업 폐업률 2025 통계")

        Returns:
            검색 결과 텍스트
        """
        self._search_count += 1
        self.log(f'검색 #{self._search_count}: {query}', 'search')

        # 진행률 업데이트 (10~90% 범위)
        progress = min(10 + (self._search_count * 5), 90)
        self.update_progress(progress, f'검색 중: {query[:30]}...')

        # Google Search grounding으로 검색
        result = self.call_gemini_with_search(
            f"다음을 검색하고 결과를 정리해주세요: {query}"
        )

        text = result.get('text') or ''
        sources = result.get('sources') or []

        # 출처 저장
        self._all_sources.extend(sources)

        # 중간 저장
        self._save_intermediate_data(query, text, sources)

        self.log(f'검색 완료: {len(sources)}개 출처', 'result', {
            'query': query,
            'sources_count': len(sources),
            'text_preview': text[:150] if text else ''
        })

        # 출처 정보 포함해서 반환
        source_info = ""
        if sources:
            source_info = "\n\n출처:\n" + "\n".join(
                f"- {s.get('title', 'N/A')}: {s.get('url', '')}"
                for s in sources[:5]
            )

        return text + source_info

    def _call_agent_with_retry(self, client, model_name, contents, config) -> any:
        """에이전트 호출 (재시도 포함)"""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # 재시도 가능한 오류인지 확인
                retriable = any(keyword in error_str for keyword in [
                    'overload', 'rate limit', 'quota', '429', '503', '500',
                    'timeout', 'unavailable', 'resource exhausted'
                ])

                if retriable and attempt < self.MAX_RETRIES - 1:
                    wait_time = self.RETRY_DELAY * (attempt + 1)
                    self.log(
                        f'API 오류 (시도 {attempt + 1}/{self.MAX_RETRIES}): {str(e)[:100]}. '
                        f'{wait_time}초 후 재시도...',
                        'error'
                    )
                    time.sleep(wait_time)
                else:
                    raise

        raise last_error

    def _run_agent(self, topic: str) -> dict:
        """에이전트 루프 실행"""
        client = self.get_client()
        model_name = self.get_model_name()

        # 검색 도구 정의
        search_tool_declaration = types.FunctionDeclaration(
            name="search_web",
            description="웹에서 정보를 검색합니다. 인용구, 통계, 사례 등을 찾을 때 사용하세요.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 내용 (예: '자영업 폐업률 2025 통계', '카센터 사장 인터뷰 한탄')"
                    }
                },
                "required": ["query"]
            }
        )

        # 이전 검색 컨텍스트
        previous_context = self._get_previous_context()

        # 대화 시작 - types.Content 형식 사용
        user_message = f"""주제: {topic}

이 주제에 대해 터지는 유튜브 영상을 만들기 위한 자료를 수집해주세요.
search_web 도구로 필요한 정보를 검색하세요.

필수 체크리스트가 모두 충족될 때까지 검색하고, 완료되면 JSON 형식으로 결과를 출력하세요.
{previous_context}"""

        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=user_message)]
            )
        ]

        # 설정
        config = types.GenerateContentConfig(
            system_instruction=RESEARCHER_SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=[search_tool_declaration])]
        )

        # 에이전트 루프 (최대 20회)
        max_iterations = 20

        for i in range(max_iterations):
            self.log(f'에이전트 턴 {i+1}/{max_iterations}', 'info')

            try:
                response = self._call_agent_with_retry(
                    client, model_name, contents, config
                )
            except Exception as e:
                self.log(f'API 최종 실패: {str(e)}', 'error')
                # 중간 결과라도 반환
                return self._build_partial_result(topic)

            # 응답 처리
            if not response.candidates:
                self.log('응답 없음', 'error')
                break

            candidate = response.candidates[0]

            # Function Call 확인
            function_calls = []
            text_response = ""

            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    function_calls.append(part.function_call)
                elif hasattr(part, 'text') and part.text:
                    text_response += part.text

            # Function Call 처리
            if function_calls:
                # 모델 응답을 contents에 추가
                contents.append(candidate.content)

                # 각 함수 호출 실행 (재시도 포함)
                function_response_parts = []
                for fc in function_calls:
                    if fc.name == "search_web":
                        query = fc.args.get("query", "")
                        result = self._search_web_with_retry(query)
                        function_response_parts.append(
                            types.Part.from_function_response(
                                name="search_web",
                                response={"result": result}
                            )
                        )

                # 함수 결과를 contents에 추가 (user role)
                contents.append(
                    types.Content(role="user", parts=function_response_parts)
                )

            else:
                # 텍스트 응답 (최종 결과)
                if text_response:
                    self.log('최종 결과 수신', 'result')
                    return self._parse_result(text_response)
                break

        self.log(f'루프 종료 (검색 {self._search_count}회)', 'info')
        return self._build_partial_result(topic)

    def _build_partial_result(self, topic: str) -> dict:
        """중간 결과로 부분 결과 생성"""
        self.log('부분 결과 생성 중...', 'info')

        # 중간 저장된 검색 결과들을 모아서 기본 구조 반환
        return {
            'topic': topic,
            'title_candidates': [],
            'best_title': {},
            'quotes': [],
            'numbers': [],
            'time_change': {},
            'person_stories': [],
            'paradox': {},
            'viewer_connection': {},
            'narrative_structure': {},
            'sources': []
        }

    def _parse_result(self, text: str) -> dict:
        """JSON 결과 파싱"""
        try:
            # JSON 블록 추출
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
            if json_match:
                return json.loads(json_match.group(1))

            # 그냥 JSON
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())

        except json.JSONDecodeError as e:
            self.log(f'JSON 파싱 실패: {str(e)}', 'error')

        return {}

    def _save_research(self, topic: str, source_url: str, result: dict):
        """Research 모델에 저장"""
        # 중복 제거된 출처
        unique_sources = []
        seen_urls = set()

        # 에이전트가 수집한 출처
        for src in self._all_sources:
            url = src.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_sources.append({
                    'title': src.get('title', ''),
                    'url': url,
                    'publisher': '',
                    'key_info': ''
                })

        # 결과에 포함된 출처
        for src in result.get('sources', []):
            url = src.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_sources.append(src)

        Research.objects.update_or_create(
            project=self.project,
            defaults={
                'source_url': source_url,
                'topic': result.get('topic', topic),
                'transcript': '',
                'summary': '',
                'title_candidates': result.get('title_candidates', []),
                'best_title': result.get('best_title', {}),
                'quotes': result.get('quotes', []),
                'numbers': result.get('numbers', []),
                'time_change': result.get('time_change', {}),
                'person_stories': result.get('person_stories', []),
                'paradox': result.get('paradox', {}),
                'viewer_connection': result.get('viewer_connection', {}),
                'narrative_structure': result.get('narrative_structure', {}),
                'sources': unique_sources[:20],
            }
        )

        self.log(f'저장 완료: 출처 {len(unique_sources)}개', 'info')
