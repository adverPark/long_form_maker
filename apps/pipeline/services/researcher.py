import time
from datetime import date
from google.genai import types
from .base import BaseStepService
from apps.pipeline.models import Research


class ResearcherService(BaseStepService):
    """리서치 에이전트 - 대본 계획의 리서치 필요 항목을 조사

    워크플로:
    1. YouTube 수집 → 2. 자막 분석 → 3. 댓글 분석 → 4. 대본 계획 → 5. 리서치

    대본 계획에서 도출된 '리서치 필요 항목'을 하나씩 검색하여 조사합니다.
    """

    agent_name = 'researcher'

    DEFAULT_PROMPT = """당신은 유튜브 콘텐츠 제작을 위한 리서치 전문가입니다.

## 🚨 핵심 원칙: 최신 정보!

- **오늘 날짜: {today}**
- 반드시 **가장 최신 자료**를 찾아주세요
- 검색할 때 "{year}" 또는 "최신"을 키워드에 포함하세요
- **수치/통계를 인용할 때는 반드시 연도를 명시**하세요
  - ❌ "폐업률이 30%에 달한다"
  - ✅ "2024년 기준 폐업률이 30%에 달한다" 또는 "2025년 1분기 폐업률이..."
- 최신 자료를 구하지 못한 경우, 해당 수치가 몇 년도 자료인지 반드시 표기하세요

## 조사 방법
1. 리서치 필요 항목을 확인합니다
2. 각 항목에 대해 search_web 도구로 검색합니다 (최신 자료 우선!)
3. 검색 결과를 정리하여 Markdown 형식으로 출력합니다

## 출력 형식 (Markdown)

# 리서치 결과

## 1. [첫 번째 항목]
- 조사 내용 (수치는 연도 명시!)
- 출처: [URL]

## 2. [두 번째 항목]
- 조사 내용
- 출처: [URL]

(이하 동일)

---

모든 항목을 조사한 후 결과를 출력해주세요."""

    # 재시도 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 30  # 초

    def __init__(self, execution):
        super().__init__(execution)
        self._search_count = 0
        self._all_sources = []

    def execute(self):
        self.update_progress(5, '리서치 시작...')

        # 대본 계획 확인 (필수)
        if not hasattr(self.project, 'research') or not self.project.research:
            raise ValueError('먼저 대본 계획을 실행해주세요.')

        content_analysis = self.project.research.content_analysis or {}
        script_plan = content_analysis.get('script_plan', '')

        if not script_plan:
            raise ValueError('대본 계획이 없습니다. 먼저 5. 대본 계획을 실행해주세요.')

        # 주제 정보
        topic_title = ''
        if hasattr(self.project, 'topic') and self.project.topic:
            topic_title = self.project.topic.title

        self.log('대본 계획 기반 리서치 시작', 'info')

        # 중간 데이터 복원
        self._restore_intermediate_data()

        if self._search_count > 0:
            self.log(f'이전 검색 {self._search_count}개 복원됨', 'info')

        self.update_progress(10, '리서치 필요 항목 조사 중...')

        # 에이전트 실행
        result_text = self._run_agent(script_plan)

        # DB에 저장 (Markdown 텍스트로)
        self.update_progress(95, '결과 저장 중...')
        self._save_research(topic_title, result_text)

        # 중간 데이터 정리
        self._clear_intermediate_data()

        self.log(f'리서치 완료 (검색 {self._search_count}회)', 'result')
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
            'summary': text,  # 전체 요약 저장 (잘리지 않음)
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
            # 전체 요약 사용 (summary 필드)
            summary = search.get('summary', search.get('text', ''))
            context += summary[:1000] + ("..." if len(summary) > 1000 else "") + "\n"

        return context

    def _search_web_with_retry(self, query: str) -> str:
        """웹 검색 (재시도 포함)"""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return self._search_web(query, is_retry=(attempt > 0))
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

    def _search_web(self, query: str, is_retry: bool = False) -> str:
        """웹 검색 도구 - Gemini가 호출함

        Args:
            query: 검색할 내용 (예: "자영업 폐업률 2025 통계")
            is_retry: 재시도인 경우 True (카운트 증가 안 함)

        Returns:
            검색 결과 텍스트
        """
        if not is_retry:
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
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                # 토큰 사용량 추적
                self.track_usage(response, model_name)
                return response
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

    def _run_agent(self, script_plan: str) -> str:
        """에이전트 루프 실행 - 대본 계획 기반 리서치

        Args:
            script_plan: 대본 계획 (리서치 필요 항목 포함)

        Returns:
            리서치 결과 (Markdown 텍스트)
        """
        client = self.get_client()
        model_name = self.get_model_name()

        # 검색 도구 정의
        search_tool_declaration = types.FunctionDeclaration(
            name="search_web",
            description="웹에서 정보를 검색합니다. 리서치 필요 항목을 조사할 때 사용하세요.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 내용"
                    }
                },
                "required": ["query"]
            }
        )

        # 이전 검색 컨텍스트
        previous_context = self._get_previous_context()

        user_message = f"""## 대본 계획

{script_plan}

---

위 대본 계획을 보고, **리서치 필요 항목**을 이해한 후 하나씩 모두 조사해주세요.

search_web 도구로 각 항목을 검색하고, 완료되면 Markdown 형식으로 결과를 정리해주세요.
{previous_context}"""

        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=user_message)]
            )
        ]

        # 설정 - 오늘 날짜 주입
        today = date.today()
        system_prompt = self.DEFAULT_PROMPT.format(
            today=today.strftime('%Y년 %m월 %d일'),
            year=today.year
        )
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
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
                return self._build_partial_result()

            # 응답 처리
            if not response.candidates:
                self.log('응답 없음', 'error')
                break

            candidate = response.candidates[0]

            # content가 없는 경우 체크
            if not candidate.content or not candidate.content.parts:
                self.log(f'응답에 content가 없음 (finish_reason: {getattr(candidate, "finish_reason", "unknown")})', 'warning')
                continue

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
                # 텍스트 응답 (최종 결과 - Markdown)
                if text_response:
                    self.log('최종 결과 수신', 'result')
                    return text_response
                break

        self.log(f'루프 종료 (검색 {self._search_count}회)', 'info')
        return self._build_partial_result()

    def _build_partial_result(self) -> str:
        """중간 검색 결과를 Markdown으로 정리"""
        self.log('부분 결과 생성 중...', 'info')

        intermediate = self.execution.intermediate_data or {}
        searches = intermediate.get('searches', [])

        if not searches:
            return "# 리서치 결과\n\n검색 결과가 없습니다."

        result = "# 리서치 결과 (부분)\n\n"
        for i, search in enumerate(searches, 1):
            result += f"## {i}. {search.get('query', '')}\n\n"
            result += search.get('summary', '') + "\n\n"

        return result

    def _save_research(self, topic_title: str, result_text: str):
        """Research 모델에 리서치 결과 저장 (content_analysis에)"""
        # 중복 제거된 출처
        unique_sources = []
        seen_urls = set()

        for src in self._all_sources:
            url = src.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_sources.append({
                    'title': src.get('title', ''),
                    'url': url,
                })

        # 기존 Research 가져오기
        research = self.project.research
        content_analysis = research.content_analysis or {}

        # 리서치 결과 추가
        content_analysis['research_result'] = result_text

        # sources도 업데이트
        research.content_analysis = content_analysis
        research.sources = unique_sources[:20]
        research.topic = topic_title or research.topic
        research.save()

        self.log(f'저장 완료: 출처 {len(unique_sources)}개', 'info')
