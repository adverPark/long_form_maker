import time
import traceback
from decimal import Decimal
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from django.conf import settings
from django.db import close_old_connections
from google import genai
from google.genai import types
from apps.prompts.models import AgentPrompt, UserAgentPrompt
from apps.accounts.models import APIKey, FreepikAccount


# API 호출 타임아웃 (초)
API_TIMEOUT = 300  # 5분 (긴 대본 처리용)


class CancelledException(Exception):
    """작업 취소 예외"""
    pass


# 사용 가능한 Gemini 모델
GEMINI_MODELS = {
    '2.5-flash': 'gemini-2.5-flash',
    '2.5-pro': 'gemini-2.5-pro',
    'flash': 'gemini-3-flash-preview',
    'pro': 'gemini-3-pro-preview',
}

# 이미지 생성 모델
IMAGE_MODELS = {
    'gemini-3-pro': 'gemini-3-pro-image-preview',
    'gemini-2.5-flash': 'gemini-2.5-flash-image',
}

# 기본 모델
DEFAULT_MODEL = 'flash'

# Gemini 가격 (USD per 1M tokens)
GEMINI_PRICING = {
    # Gemini 2.5 모델
    'gemini-2.5-flash': {
        'input': Decimal('0.30'),   # $0.30 / 1M tokens
        'output': Decimal('2.50'),  # $2.50 / 1M tokens
    },
    'gemini-2.5-pro': {
        'input': Decimal('1.25'),   # $1.25 / 1M tokens (≤200k)
        'output': Decimal('10.00'), # $10.00 / 1M tokens (≤200k)
    },
    # Gemini 3 모델
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
    # Gemini 2.5 Flash Image (이미지 출력 $30/1M tokens)
    # 이미지 1개당 약 1,290 tokens = $0.039 ≈ 43원
    'gemini-2.5-flash-image': {
        'input': Decimal('0.15'),    # $0.15 / 1M tokens
        'output': Decimal('30.00'),  # $30.00 / 1M tokens (이미지 출력)
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
            # 취소된 경우 complete() 호출하지 않음
            self.execution.refresh_from_db()
            if self.execution.status != 'cancelled':
                self.execution.complete()
        except CancelledException:
            # 취소는 정상 종료로 처리
            self.log('작업이 취소되었습니다.', 'warning')
        except Exception as e:
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.execution.fail(error_msg)
        finally:
            # 스레드에서 실행 시 DB 연결 정리
            close_old_connections()

    def check_cancelled(self) -> bool:
        """취소 여부 확인 (DB에서 최신 상태 조회)"""
        self.execution.refresh_from_db()
        return self.execution.status == 'cancelled'

    def raise_if_cancelled(self):
        """취소된 경우 예외 발생"""
        if self.check_cancelled():
            raise CancelledException('작업이 취소되었습니다.')

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
            # 모든 Gemini 키 조회
            all_keys = list(self.user.api_keys.filter(service='gemini'))
            self.log(f'Gemini 키 {len(all_keys)}개 발견: {[(k.pk, k.is_default) for k in all_keys]}')

            api_key = self.user.api_keys.filter(service='gemini', is_default=True).first()
            if not api_key:
                self.log('기본 키 없음, 첫 번째 키 사용', 'warning')
                api_key = self.user.api_keys.filter(service='gemini').first()
            if not api_key:
                raise ValueError('Gemini API 키가 설정되지 않았습니다.')

            key_value = api_key.get_key()
            self.log(f'사용할 키: ID={api_key.pk}, is_default={api_key.is_default}, key={key_value[:15]}...')
            return key_value
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

    def get_freepik_key(self) -> str:
        """사용자의 기본 Freepik API 키 가져오기"""
        try:
            api_key = self.user.api_keys.filter(service='freepik', is_default=True).first()
            if not api_key:
                api_key = self.user.api_keys.filter(service='freepik').first()
            if not api_key:
                raise ValueError('Freepik API 키가 설정되지 않았습니다.')
            return api_key.get_key()
        except APIKey.DoesNotExist:
            raise ValueError('Freepik API 키가 설정되지 않았습니다. 설정에서 API 키를 추가해주세요.')

    def get_freepik_email(self) -> str:
        """사용자의 Freepik 이메일 가져오기"""
        api_key = self.user.api_keys.filter(service='freepik_email').first()
        if not api_key:
            return ''
        return api_key.get_key()

    def get_freepik_password(self) -> str:
        """사용자의 Freepik 비밀번호 가져오기"""
        api_key = self.user.api_keys.filter(service='freepik_password').first()
        if not api_key:
            return ''
        return api_key.get_key()

    def get_freepik_account(self):
        """사용 가능한 Freepik 계정 반환 (FreepikAccount)"""
        return FreepikAccount.get_available_account(self.user)

    def get_freepik_cookie(self) -> str:
        """사용자의 Freepik 웹사이트 쿠키 가져오기 (첫 번째 활성 계정에서)"""
        account = FreepikAccount.get_available_account(self.user)
        if account:
            return account.get_cookie()
        return ''

    def get_freepik_wallet(self) -> str:
        """사용자의 Freepik Wallet ID 가져오기 (첫 번째 활성 계정에서)"""
        account = FreepikAccount.get_available_account(self.user)
        if account:
            return account.get_wallet_id()
        return ''

    def get_prompt(self) -> str:
        """프롬프트 가져오기 (사용자별 > 시스템 기본)"""
        if not self.agent_name:
            return ''

        # 1. 사용자별 커스텀 프롬프트 확인
        try:
            user_prompt = UserAgentPrompt.objects.get(
                user=self.user,
                agent_name=self.agent_name
            )
            self.log(f'사용자 커스텀 프롬프트 사용: {self.agent_name}')
            return user_prompt.prompt_content
        except UserAgentPrompt.DoesNotExist:
            pass

        # 2. 시스템 기본 프롬프트
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

    def call_gemini(self, prompt: str, model_type: str = None, max_retries: int = 3, timeout: int = API_TIMEOUT) -> str:
        """Gemini API 호출 (재시도 + 타임아웃 포함)"""
        client = self.get_client()
        model_name = self.get_model_name(model_type)

        def _call_api():
            """실제 API 호출 (타임아웃 래핑용)"""
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
            )

        last_error = None
        for attempt in range(max_retries):
            try:
                self.log(f'Gemini 호출 중... (시도 {attempt + 1}/{max_retries}, 프롬프트 {len(prompt)}자, 타임아웃 {timeout}초)')

                # 타임아웃 적용 API 호출
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_call_api)
                    try:
                        response = future.result(timeout=timeout)
                    except FuturesTimeoutError:
                        raise TimeoutError(f'API 응답 타임아웃 ({timeout}초 초과)')

                # 응답 검증
                if not response or not response.text:
                    raise ValueError('빈 응답')

                # 토큰 사용량 추적
                self.track_usage(response, model_name)

                self.log(f'Gemini 응답 완료: {len(response.text)}자')
                return response.text

            except TimeoutError as e:
                last_error = e
                self.log(f'⏱️ 타임아웃 발생 (시도 {attempt + 1}/{max_retries}): {timeout}초 초과', 'error')
                if attempt < max_retries - 1:
                    wait_time = 30 * (attempt + 1)
                    self.log(f'{wait_time}초 후 재시도...', 'warning')
                    time.sleep(wait_time)
                else:
                    self.log(f'❌ API 최종 실패: 타임아웃 ({max_retries}회 재시도 후)', 'error')
                    raise

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                error_type = type(e).__name__

                # 재시도 가능한 오류인지 확인
                retriable = any(keyword in error_str for keyword in [
                    'overload', 'rate limit', 'quota', '429', '503', '500',
                    'timeout', 'unavailable', 'resource exhausted',
                    '빈 응답', 'empty response'  # 빈 응답도 재시도
                ])

                if retriable and attempt < max_retries - 1:
                    wait_time = 30 * (attempt + 1)
                    self.log(f'API 오류 (시도 {attempt + 1}/{max_retries}): [{error_type}] {str(e)[:100]}. {wait_time}초 후 재시도...', 'warning')
                    time.sleep(wait_time)
                else:
                    self.log(f'❌ API 최종 실패: [{error_type}] {str(e)[:300]}', 'error')
                    raise

        raise last_error

    def call_gemini_json(self, prompt: str, response_schema, model_type: str = None, max_retries: int = 3, timeout: int = API_TIMEOUT) -> dict:
        """Gemini API 호출 - JSON 구조화 출력 (Pydantic 스키마 강제)

        Args:
            prompt: 프롬프트 텍스트
            response_schema: Pydantic BaseModel 클래스 (응답 스키마)
            model_type: 모델 타입 (선택)
            max_retries: 최대 재시도 횟수
            timeout: 타임아웃 (초)

        Returns:
            dict: JSON 응답 (스키마에 맞는 딕셔너리)
        """
        import json
        client = self.get_client()
        model_name = self.get_model_name(model_type)

        def _call_api():
            """실제 API 호출 (타임아웃 래핑용)"""
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                )
            )

        last_error = None
        for attempt in range(max_retries):
            try:
                self.log(f'Gemini JSON 호출 중... (시도 {attempt + 1}/{max_retries}, 스키마: {response_schema.__name__})')

                # 타임아웃 적용 API 호출
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_call_api)
                    try:
                        response = future.result(timeout=timeout)
                    except FuturesTimeoutError:
                        raise TimeoutError(f'API 응답 타임아웃 ({timeout}초 초과)')

                # 응답 검증
                if not response or not response.text:
                    raise ValueError('빈 응답')

                # 토큰 사용량 추적
                self.track_usage(response, model_name)

                # JSON 파싱
                try:
                    result = json.loads(response.text)
                    self.log(f'Gemini JSON 응답 완료: {len(response.text)}자')
                    return result
                except json.JSONDecodeError as e:
                    raise ValueError(f'JSON 파싱 실패: {e}')

            except TimeoutError as e:
                last_error = e
                self.log(f'⏱️ 타임아웃 발생 (시도 {attempt + 1}/{max_retries}): {timeout}초 초과', 'error')
                if attempt < max_retries - 1:
                    wait_time = 30 * (attempt + 1)
                    self.log(f'{wait_time}초 후 재시도...', 'warning')
                    time.sleep(wait_time)
                else:
                    raise

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                error_type = type(e).__name__

                # 재시도 가능한 오류인지 확인
                retriable = any(keyword in error_str for keyword in [
                    'overload', 'rate limit', 'quota', '429', '503', '500',
                    'timeout', 'unavailable', 'resource exhausted',
                    '빈 응답', 'empty response'
                ])

                if retriable and attempt < max_retries - 1:
                    wait_time = 30 * (attempt + 1)
                    self.log(f'API 오류 (시도 {attempt + 1}/{max_retries}): [{error_type}] {str(e)[:100]}. {wait_time}초 후 재시도...', 'warning')
                    time.sleep(wait_time)
                else:
                    self.log(f'❌ API 최종 실패: [{error_type}] {str(e)[:300]}', 'error')
                    raise

        raise last_error

    def call_gemini_with_search(self, prompt: str, model_type: str = None, max_retries: int = 3, timeout: int = API_TIMEOUT) -> dict:
        """Gemini API 호출 + Google Search grounding (재시도 + 타임아웃 포함)

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

        def _call_api():
            """실제 API 호출 (타임아웃 래핑용)"""
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[search_tool])
            )

        last_error = None
        response = None

        for attempt in range(max_retries):
            try:
                self.log(f'검색 API 호출 중... (시도 {attempt + 1}/{max_retries}, 타임아웃 {timeout}초)')

                # 타임아웃 적용 API 호출
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_call_api)
                    try:
                        response = future.result(timeout=timeout)
                    except FuturesTimeoutError:
                        raise TimeoutError(f'검색 API 응답 타임아웃 ({timeout}초 초과)')

                break  # 성공

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # 재시도 가능한 오류인지 확인
                retriable = any(keyword in error_str for keyword in [
                    'overload', 'rate limit', 'quota', '429', '503', '500',
                    'timeout', 'unavailable', 'resource exhausted',
                    '빈 응답', 'empty response'  # 빈 응답도 재시도
                ])

                if retriable and attempt < max_retries - 1:
                    wait_time = 30 * (attempt + 1)
                    self.log(f'검색 API 오류 (시도 {attempt + 1}/{max_retries}): {str(e)[:100]}. {wait_time}초 후 재시도...', 'warning')
                    time.sleep(wait_time)
                else:
                    raise

        if response is None:
            raise last_error

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
