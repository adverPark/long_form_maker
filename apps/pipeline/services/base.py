import traceback
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

    def call_gemini(self, prompt: str, model_type: str = None) -> str:
        """Gemini API 호출 (기본)"""
        client = self.get_client()
        model_name = self.get_model_name(model_type)

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
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
