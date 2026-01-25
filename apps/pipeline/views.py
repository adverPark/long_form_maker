import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.http import require_POST
from .models import (
    Project, PipelineStep, StepExecution, Topic, Research, Draft, Scene,
    ImageStylePreset, CharacterPreset, VoicePreset, ThumbnailStylePreset, UploadInfo
)
from .services import get_service_class


@login_required
def dashboard(request):
    """대시보드 - 프로젝트 목록"""
    projects = Project.objects.filter(user=request.user).prefetch_related(
        'step_executions__step'
    )

    context = {
        'projects': projects,
    }
    return render(request, 'pipeline/dashboard.html', context)


@login_required
def project_create(request):
    """새 프로젝트 생성"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, '프로젝트 이름을 입력해주세요.')
            return redirect('pipeline:dashboard')

        project = Project.objects.create(user=request.user, name=name)
        messages.success(request, f'프로젝트 "{name}"가 생성되었습니다.')
        return redirect('pipeline:project_data', pk=project.pk)

    return render(request, 'pipeline/project_create.html')


@login_required
def project_detail(request, pk):
    """프로젝트 상세 페이지 → project_data로 리다이렉트"""
    return redirect('pipeline:project_data', pk=pk)


@login_required
def step_execute(request, pk, step_name):
    """단계 실행"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    step = get_object_or_404(PipelineStep, name=step_name)

    if request.method == 'POST':
        # 이전 running 상태 실행들 취소 처리 (중복 방지)
        project.step_executions.filter(step=step, status='running').update(
            status='cancelled', progress_message='새 실행으로 대체됨'
        )

        # 이전 실행에서 토큰 가져오기 (이어서 실행 시 누적)
        prev_execution = project.step_executions.filter(step=step).order_by('-created_at').first()
        prev_tokens = {
            'input_tokens': prev_execution.input_tokens if prev_execution else 0,
            'output_tokens': prev_execution.output_tokens if prev_execution else 0,
            'total_tokens': prev_execution.total_tokens if prev_execution else 0,
            'estimated_cost': prev_execution.estimated_cost if prev_execution else 0,
        }

        # 실행 생성 (이전 토큰 이어받기)
        execution = StepExecution.objects.create(
            project=project,
            step=step,
            input_tokens=prev_tokens['input_tokens'],
            output_tokens=prev_tokens['output_tokens'],
            total_tokens=prev_tokens['total_tokens'],
            estimated_cost=prev_tokens['estimated_cost'],
        )

        # 수동 입력 처리
        manual_input = request.POST.get('manual_input', '').strip()
        model_type = request.POST.get('model_type', 'flash')

        if manual_input or model_type != 'flash':
            execution.manual_input = manual_input
            execution.model_type = model_type if model_type in ['flash', 'pro'] else 'flash'
            execution.save()

        # 이미지 프롬프트 옵션: 한글금지 체크 시 텍스트 없는 프롬프트 생성
        if step_name == 'image_prompter':
            no_text = request.POST.get('no_text') == '1'
            if no_text:
                execution.intermediate_data = {'no_text': True}
                execution.save()

        # 서비스 실행
        service_class = get_service_class(step.name)
        if service_class:
            service = service_class(execution)

            # topic_finder는 동기 실행 (빠름) - 페이지 전환 없이 바로 저장
            if step.name == 'topic_finder':
                service.run()
                if execution.status == 'completed':
                    messages.success(request, '주제가 저장되었습니다.')
                else:
                    messages.error(request, f'저장 실패: {execution.error_message[:100]}')
                return redirect('pipeline:project_data', pk=project.pk)

            # 나머지는 비동기 실행 (시간이 걸림) - 진행률 페이지로 이동
            thread = threading.Thread(target=service.run)
            thread.start()
            messages.info(request, f'{step.display_name} 실행이 시작되었습니다.')
            return redirect('pipeline:step_progress', pk=project.pk, execution_id=execution.pk)
        else:
            execution.fail(f'서비스 클래스를 찾을 수 없습니다: {step.name}')
            messages.error(request, f'서비스를 찾을 수 없습니다: {step.name}')
            return redirect('pipeline:project_data', pk=project.pk)

    # GET 요청은 프로젝트 상세로 리다이렉트
    return redirect('pipeline:project_data', pk=pk)


@login_required
def step_progress(request, pk, execution_id):
    """단계 실행 진행률 페이지"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    execution = get_object_or_404(StepExecution, pk=execution_id, project=project)

    context = {
        'project': project,
        'execution': execution,
    }
    return render(request, 'pipeline/step_progress.html', context)


@login_required
def step_progress_api(request, pk, execution_id):
    """진행률 API (AJAX용)"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    execution = get_object_or_404(StepExecution, pk=execution_id, project=project)

    return JsonResponse({
        'status': execution.status,
        'progress_percent': execution.progress_percent,
        'progress_message': execution.progress_message,
        'error_message': execution.error_message if execution.status == 'failed' else '',
        'logs': execution.logs or [],
        # 토큰 사용량
        'input_tokens': execution.input_tokens,
        'output_tokens': execution.output_tokens,
        'total_tokens': execution.total_tokens,
        'estimated_cost': float(execution.estimated_cost),
        'model_type': execution.model_type,
    })


@login_required
@require_POST
def step_cancel(request, pk, execution_id):
    """실행 취소"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    execution = get_object_or_404(StepExecution, pk=execution_id, project=project)

    if execution.status == 'running':
        execution.status = 'cancelled'
        execution.error_message = '사용자가 취소함'
        execution.save()
        return JsonResponse({'success': True, 'message': '취소되었습니다.'})

    return JsonResponse({'success': False, 'message': '실행 중인 작업이 아닙니다.'})


@login_required
@require_POST
def step_execute_parallel(request, pk):
    """여러 단계 병렬 실행 (TTS + 이미지 동시)"""
    project = get_object_or_404(Project, pk=pk, user=request.user)

    # 실행할 단계들 (POST에서 받거나 기본값)
    step_names = request.POST.getlist('steps')
    if not step_names:
        step_names = ['scene_generator', 'tts_generator']  # 기본: 이미지 + TTS

    model_type = request.POST.get('model_type', 'pro')
    executions = []

    for step_name in step_names:
        step = PipelineStep.objects.filter(name=step_name).first()
        if not step:
            continue

        # 이전 running 상태 취소
        project.step_executions.filter(step=step, status='running').update(
            status='cancelled', progress_message='새 실행으로 대체됨'
        )

        # 이전 토큰 정보 가져오기
        prev_execution = project.step_executions.filter(step=step).order_by('-created_at').first()
        prev_tokens = {
            'input_tokens': prev_execution.input_tokens if prev_execution else 0,
            'output_tokens': prev_execution.output_tokens if prev_execution else 0,
            'total_tokens': prev_execution.total_tokens if prev_execution else 0,
            'estimated_cost': prev_execution.estimated_cost if prev_execution else 0,
        }

        # 실행 생성
        execution = StepExecution.objects.create(
            project=project,
            step=step,
            model_type=model_type if step_name == 'scene_generator' else 'flash',
            input_tokens=prev_tokens['input_tokens'],
            output_tokens=prev_tokens['output_tokens'],
            total_tokens=prev_tokens['total_tokens'],
            estimated_cost=prev_tokens['estimated_cost'],
        )

        # 서비스 실행 (각각 별도 스레드)
        service_class = get_service_class(step.name)
        if service_class:
            service = service_class(execution)
            thread = threading.Thread(target=service.run)
            thread.start()
            executions.append(execution)

    if executions:
        step_names_display = ', '.join([e.step.display_name for e in executions])
        messages.info(request, f'{step_names_display} 실행이 시작되었습니다.')
        # 첫 번째 실행의 진행률 페이지로 이동 (또는 project_data로)
        return redirect('pipeline:project_data', pk=project.pk)
    else:
        messages.error(request, '실행할 단계를 찾을 수 없습니다.')
        return redirect('pipeline:project_data', pk=project.pk)


@login_required
@require_POST
def auto_pipeline(request, pk):
    """자동 파이프라인 실행 (주제 입력 후 전체 자동 생성)

    순서:
    1. 리서치 (researcher)
    2. 대본 작성 (script_writer)
    3. 씬 분할 (scene_planner)
    4. 이미지 프롬프트 + TTS (병렬)
    5. 이미지 생성 (scene_generator)
    """
    from .services.auto_pipeline import AutoPipelineService

    project = get_object_or_404(Project, pk=pk, user=request.user)

    # 주제가 없으면 에러
    if not project.topic:
        messages.error(request, '주제를 먼저 입력해주세요.')
        return redirect('pipeline:project_data', pk=project.pk)

    # auto_pipeline 스텝 생성 (없으면)
    step, _ = PipelineStep.objects.get_or_create(
        name='auto_pipeline',
        defaults={'display_name': '자동 생성', 'order': 100}
    )

    # 이전 실행에서 토큰 가져오기 (누적)
    prev_execution = project.step_executions.filter(step=step).order_by('-created_at').first()
    prev_tokens = {
        'input_tokens': prev_execution.input_tokens if prev_execution else 0,
        'output_tokens': prev_execution.output_tokens if prev_execution else 0,
        'total_tokens': prev_execution.total_tokens if prev_execution else 0,
        'estimated_cost': prev_execution.estimated_cost if prev_execution else 0,
    }

    # 실행 생성 (이전 토큰 누적)
    execution = StepExecution.objects.create(
        project=project,
        step=step,
        model_type=request.POST.get('model_type', 'pro'),
        input_tokens=prev_tokens['input_tokens'],
        output_tokens=prev_tokens['output_tokens'],
        total_tokens=prev_tokens['total_tokens'],
        estimated_cost=prev_tokens['estimated_cost'],
    )

    # 백그라운드 실행
    service = AutoPipelineService(execution)
    thread = threading.Thread(target=service.run)
    thread.start()

    messages.info(request, '자동 생성이 시작되었습니다. 완료까지 시간이 걸립니다.')
    return redirect('pipeline:step_progress', pk=project.pk, execution_id=execution.pk)


@login_required
def project_data(request, pk):
    """프로젝트 데이터 보기 (Topic, Research, Draft, Scenes)"""
    from decimal import Decimal

    project = get_object_or_404(
        Project.objects.select_related('topic', 'research', 'draft'),
        pk=pk,
        user=request.user
    )

    # 실행 중인 작업들 확인 (템플릿에서 배너로 표시) - 스텝당 최신 1개만
    running_executions = []
    seen_steps = set()
    for exec in project.step_executions.filter(status='running').select_related('step').order_by('-created_at'):
        if exec.step_id not in seen_steps:
            running_executions.append(exec)
            seen_steps.add(exec.step_id)

    # 각 단계별 최근 실행 가져오기
    steps = PipelineStep.objects.all()
    step_executions = {}
    total_tokens = 0
    total_cost = Decimal('0')

    for step in steps:
        execution = project.step_executions.filter(step=step).order_by('-created_at').first()
        step_executions[step.name] = execution
        if execution:
            total_tokens += execution.total_tokens or 0
            total_cost += execution.estimated_cost or Decimal('0')

    # 썸네일 스타일 목록 (업로드 정보에서 선택용)
    thumbnail_styles = ThumbnailStylePreset.objects.filter(user=request.user)

    context = {
        'project': project,
        'topic': getattr(project, 'topic', None),
        'research': getattr(project, 'research', None),
        'draft': getattr(project, 'draft', None),
        'scenes': project.scenes.all(),
        'steps': steps,
        'step_executions': step_executions,
        'total_tokens': total_tokens,
        'total_cost': total_cost,
        'running_executions': running_executions,  # 실행 중인 작업들 (여러 개)
        'thumbnail_styles': thumbnail_styles,
    }
    return render(request, 'pipeline/project_data.html', context)


@login_required
@require_POST
def draft_update(request, pk):
    """대본 수정 API"""
    project = get_object_or_404(Project, pk=pk, user=request.user)

    title = request.POST.get('title', '').strip()
    content = request.POST.get('content', '').strip()

    if not content:
        return JsonResponse({'success': False, 'message': '대본 내용을 입력해주세요.'})

    draft, created = Draft.objects.update_or_create(
        project=project,
        defaults={
            'title': title or '제목 없음',
            'content': content,
        }
    )

    return JsonResponse({
        'success': True,
        'message': '저장되었습니다.',
        'char_count': draft.char_count,
    })


@login_required
@require_POST
def project_delete(request, pk):
    """프로젝트 삭제"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    name = project.name
    project.delete()
    messages.success(request, f'프로젝트 "{name}"가 삭제되었습니다.')
    return redirect('pipeline:dashboard')


@login_required
def download_media(request, pk, media_type, scene_id=None):
    """미디어 파일 다운로드"""
    project = get_object_or_404(Project, pk=pk, user=request.user)

    if media_type == 'final_video' and project.final_video:
        return FileResponse(project.final_video.open('rb'), as_attachment=True)
    elif media_type == 'thumbnail' and project.thumbnail:
        return FileResponse(project.thumbnail.open('rb'), as_attachment=True)
    elif media_type == 'scene_image' and scene_id:
        scene = get_object_or_404(Scene, project=project, scene_number=scene_id)
        if scene.image:
            return FileResponse(scene.image.open('rb'), as_attachment=True)

    raise Http404('파일을 찾을 수 없습니다.')


@login_required
def project_settings(request, pk):
    """프로젝트 설정 - 프리셋 선택"""
    project = get_object_or_404(Project, pk=pk, user=request.user)

    if request.method == 'POST':
        # 이미지 모델 선택
        image_model = request.POST.get('image_model')
        if image_model in dict(Project.IMAGE_MODEL_CHOICES):
            project.image_model = image_model

        # 프리셋 선택 저장
        image_style_id = request.POST.get('image_style')
        character_id = request.POST.get('character')
        voice_id = request.POST.get('voice')
        thumbnail_style_id = request.POST.get('thumbnail_style')

        project.image_style_id = image_style_id if image_style_id else None
        project.character_id = character_id if character_id else None
        project.voice_id = voice_id if voice_id else None
        project.thumbnail_style_id = thumbnail_style_id if thumbnail_style_id else None
        project.save()

        messages.success(request, '설정이 저장되었습니다.')
        return redirect('pipeline:project_settings', pk=pk)

    context = {
        'project': project,
        'image_model_choices': Project.IMAGE_MODEL_CHOICES,
        'image_styles': ImageStylePreset.objects.filter(user=request.user),
        'characters': CharacterPreset.objects.filter(user=request.user),
        'voices': VoicePreset.objects.filter(user=request.user),
        'thumbnail_styles': ThumbnailStylePreset.objects.filter(user=request.user),
    }
    return render(request, 'pipeline/project_settings.html', context)


# 하위 호환성
image_settings = project_settings


@login_required
@require_POST
def scene_generate_image(request, pk, scene_number):
    """개별 씬 이미지 생성"""
    import io
    from PIL import Image
    from google import genai
    from google.genai import types
    from django.core.files.base import ContentFile
    from apps.accounts.models import APIKey

    project = get_object_or_404(Project, pk=pk, user=request.user)
    scene = get_object_or_404(Scene, project=project, scene_number=scene_number)

    # 프로젝트 설정에서 이미지 모델 가져오기
    from apps.pipeline.services.base import IMAGE_MODELS
    model_key = getattr(project, 'image_model', 'gemini-3-pro')
    api_model = IMAGE_MODELS.get(model_key, 'gemini-3-pro-image-preview')

    # Gemini API 키 가져오기
    api_key = APIKey.objects.filter(user=request.user, service='gemini', is_default=True).first()
    if not api_key:
        return JsonResponse({'success': False, 'message': 'Gemini API 키가 없습니다.'})

    try:
        client = genai.Client(api_key=api_key.get_key())

        # 프롬프트 구성 - 이미지 생성 명시
        base_prompt = scene.image_prompt or scene.narration or ''

        # 스타일 프리셋 적용
        style = project.image_style
        if style:
            base_prompt = f"{base_prompt}\n\nStyle: {style.style_prompt}"

        prompt = f"Generate an image based on this description:\n\n{base_prompt}\n\nAspect ratio: 16:9 (1920x1080), professional quality, photorealistic."

        # 컨텐츠 구성
        contents = [prompt]

        # 스타일 샘플 이미지 추가
        if style:
            for sample in style.sample_images.all()[:3]:
                try:
                    img = Image.open(sample.image.path)
                    contents.append(img)
                except:
                    pass

        # 캐릭터 씬이면 캐릭터 이미지 추가
        character = project.character
        if scene.has_character and character and character.image:
            try:
                char_img = Image.open(character.image.path)
                contents.append(char_img)
                contents[0] = f"Include the character from reference image. Character: {character.character_prompt}\n\n{contents[0]}"
            except:
                pass

        # Gemini 호출
        response = client.models.generate_content(
            model=api_model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE', 'TEXT'],
            )
        )

        # 이미지 추출
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                    img = Image.open(io.BytesIO(image_data))
                    img = img.resize((1920, 1080), Image.Resampling.LANCZOS)

                    output = io.BytesIO()
                    img.save(output, format='PNG')

                    filename = f'scene_{scene_number:02d}.png'
                    scene.image.save(filename, ContentFile(output.getvalue()), save=True)

                    return JsonResponse({
                        'success': True,
                        'image_url': scene.image.url
                    })

        return JsonResponse({'success': False, 'message': '이미지 생성 실패'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)[:100]})


@login_required
@require_POST
def scene_generate_tts(request, pk, scene_number):
    """개별 씬 TTS 생성"""
    import re
    import requests
    import base64
    import zipfile
    import io
    from django.conf import settings
    from django.core.files.base import ContentFile

    project = get_object_or_404(Project, pk=pk, user=request.user)
    scene = get_object_or_404(Scene, project=project, scene_number=scene_number)

    text = scene.narration_tts or scene.narration
    original_narration = scene.narration  # 자막용 원본
    if not text:
        return JsonResponse({'success': False, 'message': '나레이션이 없습니다.'})

    # 음성 프리셋
    voice = project.voice

    try:
        # API 요청 구성
        request_data = {
            'text': text,
            'format': 'wav',
            'use_memory_cache': 'on',  # 캐싱 활성화
        }

        # 프리셋 파라미터
        if voice:
            request_data['temperature'] = voice.temperature
            request_data['top_p'] = voice.top_p
            request_data['repetition_penalty'] = voice.repetition_penalty
            request_data['seed'] = voice.seed

            # 참조 음성
            if voice.reference_audio:
                with open(voice.reference_audio.path, 'rb') as f:
                    ref_audio_b64 = base64.b64encode(f.read()).decode('utf-8')
                request_data['references'] = [{
                    'audio': ref_audio_b64,
                    'text': voice.reference_text
                }]
        else:
            request_data['temperature'] = 0.7
            request_data['top_p'] = 0.7
            request_data['seed'] = 42

        response = requests.post(
            f'{settings.FISH_SPEECH_URL}/v1/tts',
            json=request_data,
            timeout=180
        )

        if response.status_code == 200:
            subtitle_status = 'none'
            subtitle_word_count = 0
            narration_word_count = len(original_narration.split()) if original_narration else 0

            # ZIP 응답 처리
            if response.content[:2] == b'PK':
                with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                    audio_data = zf.read('audio.wav')
                    scene.audio.save(f'scene_{scene_number:02d}.wav', ContentFile(audio_data), save=False)

                    # 자막 파일 추출 및 매핑
                    for name in zf.namelist():
                        if name.endswith('.srt'):
                            srt_data = zf.read(name).decode('utf-8')

                            # SRT 파싱
                            srt_pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n\n|\n*$)'
                            srt_timings = []
                            for match in re.finditer(srt_pattern, srt_data, re.DOTALL):
                                srt_timings.append({
                                    "start": match.group(2),
                                    "end": match.group(3),
                                    "text": match.group(4).strip()
                                })

                            subtitle_word_count = len(srt_timings)

                            # 원본 narration으로 매핑
                            if srt_timings and original_narration:
                                narration_words = original_narration.split()
                                mapped_entries = []
                                for i, timing in enumerate(srt_timings):
                                    word = narration_words[i] if i < len(narration_words) else timing["text"]
                                    mapped_entries.append(
                                        f'{i + 1}\n{timing["start"]} --> {timing["end"]}\n{word}\n'
                                    )
                                mapped_srt = '\n'.join(mapped_entries)

                                # 매핑된 SRT 저장
                                scene.subtitle_file.save(
                                    f'scene_{scene_number:02d}.srt',
                                    ContentFile(mapped_srt.encode('utf-8')),
                                    save=False
                                )

                                # 상태 판정
                                subtitle_status = 'matched' if subtitle_word_count == narration_word_count else 'mismatch'
                            break

                    # 자막 상태 저장
                    scene.subtitle_status = subtitle_status
                    scene.subtitle_word_count = subtitle_word_count
                    scene.narration_word_count = narration_word_count
                    scene.save()
            else:
                # 직접 WAV 응답 (자막 없음)
                scene.audio.save(f'scene_{scene_number:02d}.wav', ContentFile(response.content), save=False)
                scene.subtitle_status = 'none'
                scene.save()

            return JsonResponse({
                'success': True,
                'audio_url': scene.audio.url,
                'has_subtitle': bool(scene.subtitle_file),
                'subtitle_status': scene.subtitle_status,
                'subtitle_word_count': scene.subtitle_word_count,
                'narration_word_count': scene.narration_word_count,
            })
        else:
            return JsonResponse({'success': False, 'message': f'TTS 실패: HTTP {response.status_code}'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)[:100]})


@login_required
@require_POST
def scene_edit(request, pk, scene_number):
    """씬 편집 API - 부분 업데이트 지원"""
    from apps.pipeline.services.scene_planner import convert_to_tts

    project = get_object_or_404(Project, pk=pk, user=request.user)
    scene = get_object_or_404(Scene, project=project, scene_number=scene_number)

    updated_fields = []

    # narration 업데이트 (전달된 경우에만)
    if 'narration' in request.POST:
        narration = request.POST.get('narration', '').strip()
        scene.narration = narration
        scene.narration_tts = convert_to_tts(narration)
        updated_fields.extend(['narration', 'narration_tts'])

    # image_prompt 업데이트 (전달된 경우에만)
    if 'image_prompt' in request.POST:
        scene.image_prompt = request.POST.get('image_prompt', '').strip()
        updated_fields.append('image_prompt')

    # has_character 업데이트 (전달된 경우에만)
    if 'has_character' in request.POST:
        scene.has_character = request.POST.get('has_character') in ['true', 'True', '1', 'on']
        updated_fields.append('has_character')

    if updated_fields:
        scene.save(update_fields=updated_fields)

    return JsonResponse({
        'success': True,
        'message': '저장되었습니다.',
        'narration_tts': scene.narration_tts,
    })


@login_required
@require_POST
def scene_delete(request, pk, scene_number):
    """씬 삭제 API"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    scene = get_object_or_404(Scene, project=project, scene_number=scene_number)

    scene.delete()

    # 씬 번호 재정렬
    for i, s in enumerate(project.scenes.all().order_by('scene_number'), 1):
        if s.scene_number != i:
            s.scene_number = i
            s.save(update_fields=['scene_number'])

    return JsonResponse({
        'success': True,
        'message': f'씬 {scene_number} 삭제됨',
    })


@login_required
@require_POST
def delete_final_video(request, pk):
    """영상 제작 관련 파일 전체 삭제 (초기화)"""
    import os
    from pathlib import Path

    project = get_object_or_404(Project, pk=pk, user=request.user)

    deleted_items = []

    # 최종 영상 삭제
    if project.final_video:
        try:
            if os.path.exists(project.final_video.path):
                os.remove(project.final_video.path)
        except:
            pass
        project.final_video = None
        deleted_items.append('최종 영상')

    # 전체 자막 삭제
    if project.full_subtitles:
        try:
            if os.path.exists(project.full_subtitles.path):
                os.remove(project.full_subtitles.path)
        except:
            pass
        project.full_subtitles = None
        deleted_items.append('전체 자막')

    # 씬 영상 (인트로 영상)은 유지! Replicate 비용 들었음

    # 임시 클립들 삭제
    clips_dir = Path(settings.MEDIA_ROOT) / 'temp_clips'
    if clips_dir.exists():
        clip_count = 0
        for clip_file in clips_dir.glob(f'{project.pk}_*.mp4'):
            try:
                clip_file.unlink()
                clip_count += 1
            except:
                pass
        for txt_file in clips_dir.glob(f'{project.pk}_*.txt'):
            try:
                txt_file.unlink()
            except:
                pass
        if clip_count > 0:
            deleted_items.append(f'임시 클립 {clip_count}개')

    # ASS 자막 삭제
    ass_dir = Path(settings.MEDIA_ROOT) / 'projects' / 'subtitles' / str(project.pk)
    if ass_dir.exists():
        ass_count = 0
        for ass_file in ass_dir.glob('*.ass'):
            try:
                ass_file.unlink()
                ass_count += 1
            except:
                pass
        if ass_count > 0:
            deleted_items.append(f'ASS 자막 {ass_count}개')

    project.save()

    return JsonResponse({
        'success': True,
        'message': ', '.join(deleted_items) + ' 삭제됨' if deleted_items else '삭제할 항목 없음',
        'deleted': deleted_items,
    })


@login_required
@require_POST
def delete_all_audio(request, pk):
    """모든 씬의 오디오 삭제"""
    import os

    project = get_object_or_404(Project, pk=pk, user=request.user)

    deleted_count = 0
    for scene in project.scenes.all():
        if scene.audio:
            try:
                if os.path.exists(scene.audio.path):
                    os.remove(scene.audio.path)
            except:
                pass
            scene.audio = None

        if scene.subtitle_file:
            try:
                if os.path.exists(scene.subtitle_file.path):
                    os.remove(scene.subtitle_file.path)
            except:
                pass
            scene.subtitle_file = None

        scene.audio_duration = 0
        scene.subtitle_status = 'none'
        scene.subtitle_word_count = 0
        scene.save()
        deleted_count += 1

    return JsonResponse({
        'success': True,
        'message': f'오디오 {deleted_count}개 삭제됨',
    })


@login_required
@require_POST
def delete_all_images(request, pk):
    """모든 씬의 이미지 삭제"""
    import os

    project = get_object_or_404(Project, pk=pk, user=request.user)

    deleted_count = 0
    for scene in project.scenes.all():
        if scene.image:
            try:
                if os.path.exists(scene.image.path):
                    os.remove(scene.image.path)
            except:
                pass
            scene.image = None
            scene.save()
            deleted_count += 1

    return JsonResponse({
        'success': True,
        'message': f'이미지 {deleted_count}개 삭제됨',
    })


# =============================================
# 업로드 정보 관리
# =============================================

@login_required
def upload_info(request, pk):
    """업로드 정보 조회/수정"""
    project = get_object_or_404(Project, pk=pk, user=request.user)

    # 없으면 생성
    info, created = UploadInfo.objects.get_or_create(
        project=project,
        defaults={
            'title': project.draft.title if hasattr(project, 'draft') and project.draft else project.name,
        }
    )

    if request.method == 'POST':
        # 업로드 정보 저장
        info.title = request.POST.get('title', info.title)
        info.description = request.POST.get('description', '')
        info.thumbnail_prompt = request.POST.get('thumbnail_prompt', '')

        # 태그 파싱 (쉼표 또는 공백으로 구분)
        tags_str = request.POST.get('tags', '')
        if tags_str:
            import re
            tags = [t.strip().strip('#') for t in re.split(r'[,\s]+', tags_str) if t.strip()]
            info.tags = tags
        else:
            info.tags = []

        info.save()

        return JsonResponse({
            'success': True,
            'message': '저장되었습니다.',
        })

    return JsonResponse({
        'success': True,
        'title': info.title,
        'description': info.description,
        'tags': info.tags,
        'timeline': info.timeline,
        'thumbnail_prompt': info.thumbnail_prompt,
        'full_description': info.get_full_description(),
    })


@login_required
@require_POST
def generate_upload_info(request, pk):
    """업로드 정보 자동 생성 (LLM 사용)"""
    import re
    import json
    from decimal import Decimal
    from google import genai

    project = get_object_or_404(Project, pk=pk, user=request.user)

    # 완성도 검증
    scenes = list(project.scenes.all().order_by('scene_number'))
    if not scenes:
        return JsonResponse({'success': False, 'message': '씬이 없습니다. 씬 분할을 먼저 진행하세요.'})

    # 이미지 프롬프트 검증
    missing_prompts = [s.scene_number for s in scenes if not s.image_prompt or s.image_prompt == '[PLACEHOLDER]']
    if missing_prompts:
        return JsonResponse({
            'success': False,
            'message': f'이미지 프롬프트 없는 씬: {missing_prompts[:10]}{"..." if len(missing_prompts) > 10 else ""} (총 {len(missing_prompts)}개)'
        })

    # 이미지 검증
    missing_images = [s.scene_number for s in scenes if not s.image]
    if missing_images:
        return JsonResponse({
            'success': False,
            'message': f'이미지 없는 씬: {missing_images[:10]}{"..." if len(missing_images) > 10 else ""} (총 {len(missing_images)}개)'
        })

    # 오디오 검증
    missing_audio = [s.scene_number for s in scenes if not s.audio]
    if missing_audio:
        return JsonResponse({
            'success': False,
            'message': f'오디오 없는 씬: {missing_audio[:10]}{"..." if len(missing_audio) > 10 else ""} (총 {len(missing_audio)}개)'
        })

    # 모델 선택
    model_type = request.POST.get('model_type', 'flash')
    MODELS = {
        'flash': 'gemini-3-flash-preview',
        'pro': 'gemini-3-pro-preview',
    }
    PRICING = {
        'gemini-3-flash-preview': {'input': Decimal('0.50'), 'output': Decimal('3.00')},
        'gemini-3-pro-preview': {'input': Decimal('2.00'), 'output': Decimal('12.00')},
    }
    model_name = MODELS.get(model_type, MODELS['flash'])

    # UploadInfo 가져오거나 생성
    info, created = UploadInfo.objects.get_or_create(
        project=project,
        defaults={'title': project.name}
    )

    # 씬 정보 수집 (나레이션 + 실제 시간)
    import wave

    # scenes는 이미 위에서 가져옴
    scene_info_list = []
    current_time = 0

    for scene in scenes:
        # 실제 오디오 길이
        duration = 0
        if scene.audio:
            try:
                with wave.open(scene.audio.path, 'rb') as wav:
                    duration = wav.getnframes() / float(wav.getframerate())
            except:
                pass
        if duration == 0:
            duration = scene.audio_duration or scene.duration or 0

        scene_info_list.append({
            'scene': scene.scene_number,
            'time': current_time,
            'section': scene.section,
            'narration': scene.narration or '',
        })
        current_time += duration

    total_duration = current_time

    # 토큰 사용량 추적용
    token_info = {'input': 0, 'output': 0, 'total': 0, 'cost': '0.0000'}

    # LLM으로 제목 + 설명 + 타임라인 생성
    try:
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")
        client = genai.Client(api_key=api_key)

        # 씬 정보를 텍스트로 변환 (시간 + 나레이션)
        scenes_text = ""
        for s in scene_info_list:
            mins = int(s['time'] // 60)
            secs = int(s['time'] % 60)
            scenes_text += f"[{mins}:{secs:02d}] 씬{s['scene']} ({s['section']}): {s['narration']}\n"

        total_mins = int(total_duration // 60)
        total_secs = int(total_duration % 60)

        prompt = f"""YouTube 영상 업로드 정보를 생성해주세요.

## 영상 정보
- 총 길이: {total_mins}분 {total_secs}초
- 씬 개수: {len(scene_info_list)}개

## 전체 씬 (시간 + 나레이션)
{scenes_text}

## 생성해주세요

1. **제목** (50자 이내): 클릭 유도하는 매력적인 제목
2. **설명**: 훅(1-2문장) + 요약(3-4문장) + 구독 요청
3. **타임라인**: 섹션별 시작 시간 + 내용 기반 제목 (10자 이내)
   - intro, body_1, body_2, body_3, action, outro 각각
   - "본론 1" 같은 의미없는 제목 금지!

JSON 형식:
{{
    "title": "영상 제목",
    "description": "훅\\n\\n요약\\n\\n📌 구독과 좋아요 부탁드려요!\\n🔔 알림 설정하세요!",
    "timeline": [
        {{"time": "0:00", "title": "시작 제목"}},
        {{"time": "1:16", "title": "다음 제목"}},
        ...
    ]
}}

주의: JSON만 응답 (```json 없이)"""

        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )

        # 토큰 사용량 추출 (SDK 버전별 대응)
        input_tokens = 0
        output_tokens = 0

        # 방법 1: usage_metadata (구버전)
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage = response.usage_metadata
            input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
            output_tokens = getattr(usage, 'candidates_token_count', 0) or 0

        # 방법 2: usage (신버전)
        if not input_tokens and hasattr(response, 'usage') and response.usage:
            usage = response.usage
            input_tokens = getattr(usage, 'input_tokens', 0) or getattr(usage, 'prompt_tokens', 0) or 0
            output_tokens = getattr(usage, 'output_tokens', 0) or getattr(usage, 'completion_tokens', 0) or 0

        total_tokens = input_tokens + output_tokens

        if total_tokens > 0:
            pricing = PRICING.get(model_name, PRICING['gemini-3-flash-preview'])
            cost = (Decimal(input_tokens) / Decimal('1000000')) * pricing['input'] + \
                   (Decimal(output_tokens) / Decimal('1000000')) * pricing['output']

            token_info = {
                'input': input_tokens,
                'output': output_tokens,
                'total': total_tokens,
                'cost': f'{float(cost):.4f}',
                'model': model_name,
            }

        # JSON 파싱
        response_text = response.text.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('\n', 1)[1]
            if response_text.endswith('```'):
                response_text = response_text[:-3]

        result = json.loads(response_text)
        info.title = result.get('title', project.name)[:100]
        info.description = result.get('description', '').strip()
        info.timeline = result.get('timeline', [])

    except Exception as e:
        # LLM 실패 시 에러 반환 (조용히 넘어가지 않음)
        return JsonResponse({
            'success': False,
            'message': f'업로드 정보 생성 실패: {str(e)[:200]}'
        })

    # 태그 생성 (19금 키워드 제외)
    excluded_keywords = {'유흥', '술집', '노래방', '호프', '소주', '맥주', '주류', '성인'}
    tags = ['경제', '자영업', '재테크', '돈', '투자']

    # 제목에서 키워드 추출
    if info.title:
        words = re.findall(r'[가-힣]+', info.title)
        for word in words:
            if len(word) >= 2 and word not in excluded_keywords and word not in tags:
                tags.append(word)
                if len(tags) >= 15:
                    break

    info.tags = tags[:15]

    # 썸네일 프롬프트 생성 (LLM으로 별도 생성)
    try:
        # 인트로 씬들의 나레이션으로 핵심 내용 파악
        intro_narrations = [s['narration'] for s in scene_info_list[:5]]
        intro_text = ' '.join(intro_narrations)[:500]

        thumb_prompt = f"""YouTube 썸네일 이미지 생성 프롬프트를 영어로 작성해주세요.

영상 제목: {info.title}
영상 시작 내용: {intro_text}

요구사항:
1. 클릭을 유도하는 강렬한 이미지
2. 한글 텍스트 10자 이내 포함
3. 경제/돈 관련 시각적 요소
4. 감정: 충격, 호기심, 긴박감 중 택1

프롬프트만 출력 (설명 없이, 색상 지정 없이):"""

        thumb_response = client.models.generate_content(
            model=model_name,
            contents=thumb_prompt
        )
        info.thumbnail_prompt = thumb_response.text.strip()

    except Exception as e:
        # 실패 시 기본 프롬프트
        info.thumbnail_prompt = f"""YouTube thumbnail for Korean economy video.

Main visual: dramatic money/finance scene with urgency
Korean text: '{info.title[:10] if info.title else "경제"}'
Style: clickbait youtube thumbnail, high contrast, dramatic lighting
Emotion: shock, curiosity

Technical: 1280x720, clean composition, mobile-friendly text size"""

    info.save()

    return JsonResponse({
        'success': True,
        'message': '업로드 정보가 생성되었습니다.',
        'title': info.title,
        'description': info.description,
        'tags': info.tags,
        'timeline': info.timeline,
        'thumbnail_prompt': info.thumbnail_prompt,
        'full_description': info.get_full_description(),
        'token_info': token_info,
    })


@login_required
@require_POST
def generate_thumbnail(request, pk):
    """썸네일 생성"""
    import io
    from PIL import Image
    from django.core.files.base import ContentFile
    from google import genai
    from google.genai import types

    project = get_object_or_404(Project, pk=pk, user=request.user)

    # 프롬프트 가져오기
    prompt = request.POST.get('prompt', '')
    if not prompt:
        # UploadInfo에서 가져오기
        if hasattr(project, 'upload_info') and project.upload_info:
            prompt = project.upload_info.thumbnail_prompt
        if not prompt:
            return JsonResponse({'success': False, 'message': '썸네일 프롬프트가 없습니다.'})

    # 썸네일 스타일 선택 (직접 지정 > 프로젝트 설정)
    style_id = request.POST.get('style_id', '')
    thumbnail_style = None
    if style_id:
        thumbnail_style = ThumbnailStylePreset.objects.filter(pk=style_id, user=request.user).first()
    if not thumbnail_style:
        thumbnail_style = project.thumbnail_style

    try:
        # Gemini 클라이언트
        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        # 프롬프트에 기술 요구사항 추가
        full_prompt = f"""{prompt}

IMPORTANT: Generate a 16:9 aspect ratio image (1280x720 pixels).
Korean text must be clearly readable with bold font and high contrast."""

        contents = [full_prompt]

        # 썸네일 스타일의 예시 이미지 추가
        if thumbnail_style and thumbnail_style.example_image:
            try:
                example_img = Image.open(thumbnail_style.example_image.path)
                contents.append(example_img)
                contents[0] = f"Create a thumbnail in the same style as the reference image.\n\n{contents[0]}"
            except:
                pass

        # 캐릭터 이미지 추가 (있으면)
        if project.character and project.character.image:
            try:
                char_img = Image.open(project.character.image.path)
                contents.append(char_img)
                contents[0] = f"Include the character from reference. {project.character.character_prompt}\n\n{contents[0]}"
            except:
                pass

        # Gemini 호출
        response = client.models.generate_content(
            model='gemini-3-pro-image-preview',
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE', 'TEXT'],
            )
        )

        # 이미지 추출
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                    img = Image.open(io.BytesIO(image_data))
                    img = img.resize((1280, 720), Image.Resampling.LANCZOS)

                    output = io.BytesIO()
                    img.save(output, format='PNG')

                    project.thumbnail.save('thumbnail.png', ContentFile(output.getvalue()), save=True)

                    return JsonResponse({
                        'success': True,
                        'thumbnail_url': project.thumbnail.url,
                    })

        return JsonResponse({'success': False, 'message': '썸네일 생성 실패'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)[:100]})
