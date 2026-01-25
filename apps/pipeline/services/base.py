import traceback
from decimal import Decimal
from abc import ABC, abstractmethod
from django.conf import settings
from google import genai
from google.genai import types
from apps.prompts.models import AgentPrompt
from apps.accounts.models import APIKey


# 사용 가능한 Gemini 모델
GEMINI_MODELS = {
    'flash': 'gemini-3-flash-preview',
    'pro': 'gemini-3-pro-preview',
}

# 기본 모델
DEFAULT_MODEL = 'flash'

# Gemini 가격 (USD per 1M tokens)
GEMINI_PRICING = {
    'gemini-3-flash-preview': {
        'input': Decimal('0.50'),   # $0.50 / 1M tokens
        'output': Decimal('3.00'),  # $3.00 / 1M tokens
    },
    'gemini-3-pro-preview': {
        'input': Decimal('2.00'),   # $2.00 / 1M tokens
        'output': Decimal('12.00'), # $12.00 / 1M tokens (텍스트)
    },
    # 이미지 생성 모델 Pro (이미지 출력은 $120/1M tokens - Vertex AI 기준)
    # 이미지 1개당 약 1,120 tokens = $0.134 ≈ 150원
    'gemini-3-pro-image-preview': {
        'input': Decimal('2.00'),    # $2.00 / 1M tokens
        'output': Decimal('120.00'), # $120.00 / 1M tokens (이미지 출력)
    },
    # 이미지 생성 모델 Flash (훨씬 저렴)
    'gemini-2.0-flash': {
        'input': Decimal('0.10'),    # $0.10 / 1M tokens
        'output': Decimal('0.40'),   # $0.40 / 1M tokens
    },
    'gemini-2.0-flash-exp-image-generation': {
        'input': Decimal('0.10'),    # Flash 기본 가격 적용
        'output': Decimal('0.40'),   # Flash 기본 가격 적용
    },
}


class BaseStepService(ABC):
    """단계 실행 서비스 베이스 클래스"""

    agent_name: str = None  # 서브클래스에서 정의

    def __init__(self, execution):
        self.execution = execution
        self.project = execution.project
        self.user = execution.project.user
        self._client = None

    def run(self):
        """실행 (에러 핸들링 포함)"""
        try:
            self.execution.start()
            self.execute()
            self.execution.complete()
        except Exception as e:
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.execution.fail(error_msg)

    @abstractmethod
    def execute(self):
        """실제 실행 로직 (서브클래스에서 구현)"""
        pass

    def update_progress(self, percent: int, message: str = ''):
        """진행률 업데이트"""
        self.execution.update_progress(percent, message)

    def log(self, message: str, log_type: str = 'info', data: dict = None):
        """로그 추가 (실시간 확인용)"""
        self.execution.add_log(log_type, message, data)

    def get_manual_input(self) -> str:
        """수동 입력 데이터 가져오기"""
        return self.execution.manual_input or ''

    def get_gemini_key(self) -> str:
        """사용자의 기본 Gemini API 키 가져오기"""
        try:
            api_key = self.user.api_keys.filter(service='gemini', is_default=True).first()
            if not api_key:
                api_key = self.user.api_keys.filter(service='gemini').first()
            if not api_key:
                raise ValueError('Gemini API 키가 설정되지 않았습니다.')
            return api_key.get_key()
        except APIKey.DoesNotExist:
            raise ValueError('Gemini API 키가 설정되지 않았습니다. 설정에서 API 키를 추가해주세요.')

    def get_replicate_key(self) -> str:
        """사용자의 기본 Replicate API 키 가져오기"""
        try:
            api_key = self.user.api_keys.filter(service='replicate', is_default=True).first()
            if not api_key:
                api_key = self.user.api_keys.filter(service='replicate').first()
            if not api_key:
                raise ValueError('Replicate API 키가 설정되지 않았습니다.')
            return api_key.get_key()
        except APIKey.DoesNotExist:
            raise ValueError('Replicate API 키가 설정되지 않았습니다. 설정에서 API 키를 추가해주세요.')

    def get_prompt(self) -> str:
        """활성화된 프롬프트 가져오기"""
        if not self.agent_name:
            return ''
        try:
            prompt = AgentPrompt.objects.get(agent_name=self.agent_name, is_active=True)
            return prompt.prompt_content
        except AgentPrompt.DoesNotExist:
            return ''

    def get_user_model_preference(self) -> str:
        """모델 선택 가져오기 (실행 > 사용자 설정 > 기본값)"""
        # 1. 실행 시 지정한 모델
        if hasattr(self.execution, 'model_type') and self.execution.model_type:
            return self.execution.model_type
        # 2. 사용자 기본 설정
        return getattr(self.user, 'gemini_model', DEFAULT_MODEL)

    def get_client(self) -> genai.Client:
        """Gemini 클라이언트 가져오기 (싱글톤)"""
        if self._client is None:
            self._client = genai.Client(api_key=self.get_gemini_key())
        return self._client

    def get_model_name(self, model_type: str = None) -> str:
        """모델 이름 가져오기"""
        model_type = model_type or self.get_user_model_preference()
        return GEMINI_MODELS.get(model_type, GEMINI_MODELS[DEFAULT_MODEL])

    def track_usage(self, response, model_name: str = None):
        """토큰 사용량 추적 및 비용 계산

        Args:
            response: Gemini API 응답 객체
            model_name: 사용한 모델명
        """
        model_name = model_name or self.get_model_name()

        # 토큰 정보 추출 시도
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0

        # 방법 1: usage_metadata (구 버전)
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage = response.usage_metadata
            input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
            output_tokens = getattr(usage, 'candidates_token_count', 0) or 0
            total_tokens = getattr(usage, 'total_token_count', 0) or 0

        # 방법 2: usage (신 버전 SDK)
        if not total_tokens and hasattr(response, 'usage') and response.usage:
            usage = response.usage
            input_tokens = getattr(usage, 'input_tokens', 0) or getattr(usage, 'prompt_tokens', 0) or 0
            output_tokens = getattr(usage, 'output_tokens', 0) or getattr(usage, 'completion_tokens', 0) or 0
            total_tokens = input_tokens + output_tokens

        # 방법 3: model_dump() 또는 dict로 변환해서 찾기
        if not total_tokens:
            try:
                if hasattr(response, 'model_dump'):
                    resp_dict = response.model_dump()
                elif hasattr(response, '__dict__'):
                    resp_dict = response.__dict__
                else:
                    resp_dict = {}

                # usage_metadata 키 찾기
                usage_data = resp_dict.get('usage_metadata') or resp_dict.get('usage') or {}
                if usage_data:
                    input_tokens = usage_data.get('prompt_token_count') or usage_data.get('input_tokens') or 0
                    output_tokens = usage_data.get('candidates_token_count') or usage_data.get('output_tokens') or 0
                    total_tokens = usage_data.get('total_token_count') or (input_tokens + output_tokens)
            except Exception:
                pass

        if not total_tokens:
            # 토큰 정보를 찾지 못함 - 로그 남기기
            self.log(f'토큰 정보 없음 (response type: {type(response).__name__})', 'info')
            return

        # 누적
        self.execution.input_tokens += input_tokens
        self.execution.output_tokens += output_tokens
        self.execution.total_tokens += total_tokens

        # 비용 계산
        pricing = GEMINI_PRICING.get(model_name, GEMINI_PRICING['gemini-3-flash-preview'])
        input_cost = (Decimal(input_tokens) / Decimal('1000000')) * pricing['input']
        output_cost = (Decimal(output_tokens) / Decimal('1000000')) * pricing['output']
        self.execution.estimated_cost += input_cost + output_cost

        # DB 저장
        self.execution.save(update_fields=[
            'input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost'
        ])

        # 로그
        self.log(f'토큰: {input_tokens:,} + {output_tokens:,} = {total_tokens:,} (${float(self.execution.estimated_cost):.4f})')

    def call_gemini(self, prompt: str, model_type: str = None) -> str:
        """Gemini API 호출 (기본)"""
        client = self.get_client()
        model_name = self.get_model_name(model_type)

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )

        # 토큰 사용량 추적
        self.track_usage(response, model_name)

        return response.text

    def call_gemini_with_search(self, prompt: str, model_type: str = None) -> dict:
        """Gemini API 호출 + Google Search grounding

        Returns:
            dict: {
                'text': 응답 텍스트,
                'sources': [{'url': ..., 'title': ...}, ...],
                'search_queries': [검색 쿼리들]
            }
        """
        client = self.get_client()
        model_name = self.get_model_name(model_type)

        # Google Search 도구 설정
        search_tool = types.Tool(
            google_search=types.GoogleSearch()
        )

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[search_tool])
        )

        # 토큰 사용량 추적
        self.track_usage(response, model_name)

        # 결과 파싱
        result = {
            'text': response.text,
            'sources': [],
            'search_queries': []
        }

        # grounding metadata에서 출처 추출
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
                metadata = candidate.grounding_metadata

                # 검색 쿼리
                if hasattr(metadata, 'web_search_queries'):
                    result['search_queries'] = list(metadata.web_search_queries or [])

                # 출처
                if hasattr(metadata, 'grounding_chunks'):
                    for chunk in (metadata.grounding_chunks or []):
                        if hasattr(chunk, 'web') and chunk.web:
                            result['sources'].append({
                                'url': chunk.web.uri,
                                'title': chunk.web.title
                            })

        return result
