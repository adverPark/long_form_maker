import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.http import require_POST
from .models import (
    Project, PipelineStep, StepExecution, Topic, Research, Draft, Scene,
    ImageStylePreset, CharacterPreset, VoicePreset
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

    # 실행 생성
    execution = StepExecution.objects.create(
        project=project,
        step=step,
        model_type=request.POST.get('model_type', 'pro'),
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
        # 프리셋 선택 저장
        image_style_id = request.POST.get('image_style')
        character_id = request.POST.get('character')
        voice_id = request.POST.get('voice')

        project.image_style_id = image_style_id if image_style_id else None
        project.character_id = character_id if character_id else None
        project.voice_id = voice_id if voice_id else None
        project.save()

        messages.success(request, '설정이 저장되었습니다.')
        return redirect('pipeline:project_settings', pk=pk)

    context = {
        'project': project,
        'image_styles': ImageStylePreset.objects.filter(user=request.user),
        'characters': CharacterPreset.objects.filter(user=request.user),
        'voices': VoicePreset.objects.filter(user=request.user),
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

    # 모델 선택
    model_type = request.POST.get('model_type', 'pro')
    IMAGE_MODELS = {
        'pro': 'gemini-3-pro-image-preview',
        'flash': 'gemini-2.0-flash-exp-image-generation',
    }
    api_model = IMAGE_MODELS.get(model_type, IMAGE_MODELS['pro'])

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
