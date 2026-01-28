import io
import os
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from django.core.files.base import ContentFile
from google.genai import types
import replicate
from .base import BaseStepService
from apps.pipeline.models import Scene


class SceneGeneratorService(BaseStepService):
    """씬 이미지 생성 서비스

    핵심 원칙:
    - Project.image_model 설정에 따라 모델 선택:
      - gemini-3-pro: 고품질, 한글 OK ($0.134/장)
      - gemini-2.5-flash: 저렴, 한글 불안정 ($0.039/장)
    - Project의 프리셋 설정 사용:
      - image_style: 스타일 프롬프트 + 샘플 이미지
      - character: 캐릭터 이미지 + 프롬프트 (캐릭터 씬에만)
    - 5개씩 병렬 처리
    """

    agent_name = 'scene_generator'
    BATCH_SIZE = 5  # 병렬 처리 배치 크기

    # 이미지 생성 모델 매핑
    IMAGE_MODELS = {
        'gemini-3-pro': {
            'provider': 'gemini',
            'api_model': 'gemini-3-pro-image-preview',
            'pricing_model': 'gemini-3-pro-image-preview',
        },
        'gemini-2.5-flash': {
            'provider': 'gemini',
            'api_model': 'gemini-2.5-flash-image',
            'pricing_model': 'gemini-2.5-flash-image',
        },
        'flux-schnell': {
            'provider': 'replicate',
            'api_model': 'black-forest-labs/flux-schnell',
            'price_per_image': 0.003,
        },
        'sdxl': {
            'provider': 'replicate',
            'api_model': 'stability-ai/sdxl:7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc',
            'price_per_image': 0.005,
        },
    }

    def get_image_model_config(self) -> dict:
        """프로젝트 설정에서 이미지 모델 가져오기"""
        model_key = getattr(self.project, 'image_model', 'gemini-3-pro')
        return self.IMAGE_MODELS.get(model_key, self.IMAGE_MODELS['gemini-3-pro'])

    def execute(self):
        self.update_progress(5, '씬 로딩 중...')
        self._lock = threading.Lock()  # 스레드 안전을 위한 락

        # 프로젝트 설정에서 이미지 모델 가져오기
        model_config = self.get_image_model_config()
        model_key = getattr(self.project, 'image_model', 'gemini-3-pro')
        provider = model_config.get('provider', 'gemini')
        self.log(f'이미지 생성 시작 (모델: {model_key}, provider: {provider}, API: {model_config["api_model"]})')

        # Replicate 모델인 경우 API 키 미리 가져오기
        self._replicate_key = None
        if provider == 'replicate':
            self._replicate_key = self.get_replicate_key()
            self.log(f'Replicate API 키 확인됨')

        # DB에서 씬 가져오기
        scenes = list(self.project.scenes.all().order_by('scene_number'))

        if not scenes:
            raise ValueError('씬이 없습니다. 씬 분할을 먼저 완료해주세요.')

        total = len(scenes)
        self.log(f'총 {total}개 씬 로드')

        # 프리셋 정보 로깅
        style = self.project.image_style
        character = self.project.character
        self.log(f'설정: 스타일={style.name if style else "없음"}, '
                 f'캐릭터={character.name if character else "없음"}')

        # 생성할 씬 필터링
        scenes_to_process = []
        skip_image_exists = 0
        skip_no_prompt = 0

        for scene in scenes:
            if scene.image:
                self.log(f'씬 {scene.scene_number} 건너뜀 - 이미지 존재')
                skip_image_exists += 1
                continue

            # 프롬프트가 없거나 PLACEHOLDER면 건너뜀
            prompt = scene.image_prompt or ''
            if not prompt or prompt == '[PLACEHOLDER]' or len(prompt.strip()) < 20:
                self.log(f'씬 {scene.scene_number} 건너뜀 - 프롬프트 없음/부족', 'warning')
                skip_no_prompt += 1
                continue

            scenes_to_process.append(scene)

        if not scenes_to_process:
            msg = f'생성할 씬 없음 (이미지 존재: {skip_image_exists}, 프롬프트 없음: {skip_no_prompt})'
            self.log(msg)
            self.update_progress(100, msg)
            return

        self.log(f'{len(scenes_to_process)}개 씬 이미지 생성 예정 (배치 크기: {self.BATCH_SIZE})')

        # 배치 병렬 처리
        success_count = 0
        error_count = 0
        processed = 0

        for batch_start in range(0, len(scenes_to_process), self.BATCH_SIZE):
            batch = scenes_to_process[batch_start:batch_start + self.BATCH_SIZE]
            batch_nums = [s.scene_number for s in batch]
            self.log(f'배치 처리 중: 씬 {batch_nums}')

            with ThreadPoolExecutor(max_workers=self.BATCH_SIZE) as executor:
                future_to_scene = {
                    executor.submit(
                        self._generate_scene_image_thread,
                        scene,
                        model_config,
                    ): scene
                    for scene in batch
                }

                for future in as_completed(future_to_scene):
                    scene = future_to_scene[future]
                    scene_num = scene.scene_number
                    processed += 1

                    try:
                        image_data = future.result()
                        if image_data:
                            filename = f'scene_{scene_num:02d}.png'
                            # save=False로 파일만 저장, update_fields로 해당 필드만 업데이트
                            scene.image.save(filename, ContentFile(image_data), save=False)
                            Scene.objects.filter(pk=scene.pk).update(image=scene.image.name)
                            with self._lock:
                                self.log(f'씬 {scene_num} 저장 완료')
                            success_count += 1
                        else:
                            with self._lock:
                                self.log(f'씬 {scene_num} 생성 실패', 'error')
                            error_count += 1
                    except Exception as e:
                        with self._lock:
                            self.log(f'씬 {scene_num} 오류: {str(e)[:50]}', 'error')
                        error_count += 1

                    progress = 5 + int((processed / len(scenes_to_process)) * 90)
                    self.update_progress(progress, f'{processed}/{len(scenes_to_process)} 이미지 생성 중...')

        # 완료
        self.log(f'이미지 생성 완료', 'result', {
            'total': total,
            'completed': success_count,
            'errors': error_count,
            'skip_image_exists': skip_image_exists,
            'skip_no_prompt': skip_no_prompt
        })

        # 실패 처리 - 에러가 성공보다 많거나 모두 실패한 경우
        scenes_attempted = len(scenes_to_process)
        if scenes_attempted > 0:
            if success_count == 0:
                raise ValueError(f'이미지 생성 실패: {scenes_attempted}개 씬 시도했으나 모두 실패')
            elif error_count > success_count:
                raise ValueError(f'이미지 생성 실패: {scenes_attempted}개 중 {error_count}개 실패 (성공: {success_count}개)')

        # 에러가 있으면 메시지에 표시
        skip_msg = f', 스킵: {skip_image_exists + skip_no_prompt}' if (skip_image_exists + skip_no_prompt) > 0 else ''
        if error_count > 0:
            self.update_progress(100, f'완료: {success_count}개 생성, ⚠️ {error_count}개 실패{skip_msg}')
        else:
            self.update_progress(100, f'완료: {success_count}개 생성{skip_msg}')

    def _generate_scene_image_thread(self, scene: Scene, model_config: dict) -> bytes:
        """병렬 처리용 이미지 생성 (provider에 따라 다른 방식 사용)"""
        provider = model_config.get('provider', 'gemini')

        if provider == 'replicate':
            return self._generate_replicate_image(scene, model_config)
        else:
            client = self.get_client()  # Gemini용 클라이언트
            return self._generate_scene_image(client, scene, model_config)

    def _thread_log(self, message, log_type='info'):
        """스레드 안전 로그"""
        if hasattr(self, '_lock'):
            with self._lock:
                self.log(message, log_type)
        else:
            self.log(message, log_type)

    def _thread_track_usage(self, response, pricing_model):
        """스레드 안전 토큰 추적"""
        if hasattr(self, '_lock'):
            with self._lock:
                self.track_usage(response, pricing_model)
        else:
            self.track_usage(response, pricing_model)

    def _generate_scene_image(self, client, scene: Scene, model_config: dict = None) -> bytes:
        """씬 이미지 생성

        Args:
            client: Gemini 클라이언트
            scene: 씬 모델
            model_config: 모델 설정 (api_model, pricing_model 등)

        Returns:
            이미지 바이트 데이터 또는 None
        """
        if model_config is None:
            model_config = self.get_image_model_config()

        scene_num = scene.scene_number

        # 프롬프트 구성 - 상황 묘사에 집중 (캐릭터/스타일은 이미지로 제공)
        base_prompt = scene.image_prompt or ''

        # 16:9 고정 + 이미지 생성 명시
        prompt = f"Generate an image based on this description:\n\n{base_prompt}\n\nAspect ratio: 16:9 (1920x1080), professional quality."

        # 컨텐츠 구성 (텍스트 + 참조 이미지들)
        contents = [prompt]

        # 스타일 샘플 이미지 추가
        style = self.project.image_style
        style_images_added = 0
        if style:
            for sample in style.sample_images.all()[:3]:  # 최대 3개
                try:
                    img = Image.open(sample.image.path)
                    contents.append(img)
                    style_images_added += 1
                except Exception as e:
                    self._thread_log(f'씬{scene_num} 스타일 샘플 로드 실패: {e}', 'error')

            # 스타일 참조 지시 추가 (이미지 설명 포함)
            if style_images_added > 0:
                style_desc = style.style_prompt if style.style_prompt else "the reference images"
                contents[0] = f"Use the reference images for background and artistic style. Style: {style_desc}\n\n{contents[0]}"

        # 캐릭터 씬이면 캐릭터 이미지 추가
        character = self.project.character
        if scene.has_character and character and character.image:
            try:
                char_img = Image.open(character.image.path)
                contents.append(char_img)
                # 캐릭터 참조 지시 추가
                contents[0] = f"Include the character from the reference image.\n\n{contents[0]}"
            except Exception as e:
                self._thread_log(f'씬{scene_num} 캐릭터 이미지 로드 실패: {e}', 'error')

        # Gemini 호출 (재시도 포함)
        max_retries = 3
        api_model = model_config['api_model']
        pricing_model = model_config['pricing_model']

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=api_model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=['IMAGE', 'TEXT'],
                    )
                )

                # 이미지 추출
                if hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]

                    # 안전 필터 체크
                    if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                        blocked = [r for r in candidate.safety_ratings if hasattr(r, 'blocked') and r.blocked]
                        if blocked:
                            self._thread_log(f'씬{scene_num} 안전 필터 차단', 'error')

                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            image_data = part.inline_data.data
                            # 1920x1080으로 리사이즈
                            img = Image.open(io.BytesIO(image_data))
                            img = img.resize((1920, 1080), Image.Resampling.LANCZOS)

                            output = io.BytesIO()
                            img.save(output, format='PNG')

                            # 성공 시에만 토큰 추적!
                            self._thread_track_usage(response, pricing_model)
                            return output.getvalue()

                        # 텍스트 응답이 있으면 로깅
                        if hasattr(part, 'text') and part.text:
                            self._thread_log(f'씬{scene_num} 텍스트 응답: {part.text[:100]}', 'warning')

                    self._thread_log(f'씬{scene_num} 이미지 응답 없음', 'error')
                else:
                    # candidates 자체가 없음
                    if hasattr(response, 'prompt_feedback'):
                        self._thread_log(f'씬{scene_num} 프롬프트 거부', 'error')
                    else:
                        self._thread_log(f'씬{scene_num} 응답 없음', 'error')

            except Exception as e:
                self._thread_log(f'씬{scene_num} 시도{attempt + 1} 실패: {str(e)[:50]}', 'error')
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 지수 백오프

        # 모든 시도 실패
        return None

    def _generate_replicate_image(self, scene: Scene, model_config: dict) -> bytes:
        """Replicate API로 이미지 생성 (FLUX.1-schnell, SDXL 등)

        Args:
            scene: 씬 모델
            model_config: 모델 설정

        Returns:
            이미지 바이트 데이터 또는 None
        """
        scene_num = scene.scene_number
        api_model = model_config['api_model']

        # 프롬프트 구성 - 상황 묘사에 집중
        base_prompt = scene.image_prompt or ''

        # 16:9 비율 명시
        prompt = f"{base_prompt}, 16:9 aspect ratio, professional quality"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                client = replicate.Client(api_token=self._replicate_key)

                # FLUX.1-schnell과 SDXL은 입력 파라미터가 다름
                if 'flux-schnell' in api_model:
                    output = client.run(
                        api_model,
                        input={
                            "prompt": prompt,
                            "num_outputs": 1,
                            "aspect_ratio": "16:9",
                            "output_format": "png",
                            "output_quality": 90,
                        }
                    )
                elif 'sdxl' in api_model:
                    output = client.run(
                        api_model,
                        input={
                            "prompt": prompt,
                            "width": 1344,  # 16:9에 가까운 SDXL 지원 해상도
                            "height": 768,
                            "num_outputs": 1,
                            "scheduler": "K_EULER",
                            "num_inference_steps": 25,
                        }
                    )
                else:
                    # 일반적인 경우
                    output = client.run(
                        api_model,
                        input={
                            "prompt": prompt,
                            "num_outputs": 1,
                        }
                    )

                # 결과 처리 (URL 또는 FileOutput)
                if output:
                    image_url = output[0] if isinstance(output, list) else output

                    # FileOutput 객체인 경우 URL 추출
                    if hasattr(image_url, 'url'):
                        image_url = image_url.url

                    # URL에서 이미지 다운로드
                    response = requests.get(str(image_url), timeout=30)
                    response.raise_for_status()

                    # 1920x1080으로 리사이즈
                    img = Image.open(io.BytesIO(response.content))
                    img = img.resize((1920, 1080), Image.Resampling.LANCZOS)

                    output_buffer = io.BytesIO()
                    img.save(output_buffer, format='PNG')

                    self._thread_log(f'씬{scene_num} Replicate 생성 완료')

                    # 비용 추적 (간단히 로그만)
                    price = model_config.get('price_per_image', 0)
                    if price > 0:
                        self._thread_log(f'씬{scene_num} 예상 비용: ${price:.4f}')

                    return output_buffer.getvalue()

                self._thread_log(f'씬{scene_num} Replicate 응답 없음', 'error')

            except replicate.exceptions.ReplicateError as e:
                self._thread_log(f'씬{scene_num} Replicate 에러: {str(e)[:100]}', 'error')
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
            except Exception as e:
                self._thread_log(f'씬{scene_num} 시도{attempt + 1} 실패: {str(e)[:50]}', 'error')
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        return None
