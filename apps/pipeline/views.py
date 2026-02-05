import logging
import threading
from django.shortcuts import render, redirect, get_object_or_404

logger = logging.getLogger(__name__)
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.http import require_POST
from .models import (
    Project, PipelineStep, StepExecution, Topic, Research, Draft, Scene,
    ImageStylePreset, CharacterPreset, VoicePreset, ThumbnailStylePreset, UploadInfo,
    YouTubeComment
)
from .services import get_service_class
from apps.accounts.models import APIKey


def _cleanup_stale_executions(user=None):
    """오래된 running 상태 실행을 failed로 변경 (스레드 죽은 경우 대비)"""
    from django.utils import timezone
    from datetime import timedelta

    # 30분 이상 실행 중인 작업 정리 (로그 시간은 타임존 이슈로 사용 안함)
    stale_threshold = timezone.now() - timedelta(minutes=30)

    query = StepExecution.objects.filter(status='running', created_at__lt=stale_threshold)
    if user:
        query = query.filter(project__user=user)

    for exec in query:
        exec.status = 'failed'
        exec.error_message = '30분 이상 실행 중 - 서버 재시작 또는 스레드 종료로 인해 중단됨'
        exec.save()


@login_required
def dashboard(request):
    """대시보드 - 프로젝트 목록"""
    # stale 상태 정리 (스레드 죽은 running 실행들)
    _cleanup_stale_executions(user=request.user)

    projects = Project.objects.filter(user=request.user).prefetch_related(
        'step_executions__step'
    )

    # 진행 중 + 실패 + 완료(미확인) 작업 목록
    running_executions = []
    seen_keys = set()
    for exec in StepExecution.objects.filter(
        project__user=request.user
    ).select_related('project', 'step').order_by('-created_at')[:50]:
        key = (exec.project_id, exec.step_id)
        if key not in seen_keys:
            seen_keys.add(key)
            # running, failed는 항상 표시 / completed는 acknowledged=False일 때만
            if exec.status in ['running', 'failed']:
                running_executions.append(exec)
            elif exec.status == 'completed' and not exec.acknowledged:
                running_executions.append(exec)
        if len(running_executions) >= 10:
            break

    context = {
        'projects': projects,
        'running_executions': running_executions,
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
        # 이미 실행 중인 작업이 있으면 차단
        running_exec = project.step_executions.filter(step=step, status='running').first()
        if running_exec:
            message = f'{step.display_name}이(가) 이미 실행 중입니다. 취소 후 다시 시도해주세요.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': message,
                    'execution_id': running_exec.pk,
                })
            messages.warning(request, message)
            return redirect('pipeline:step_progress', pk=project.pk, execution_id=running_exec.pk)

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
            input_tokens=prev_tokens['input_tokens'],
            output_tokens=prev_tokens['output_tokens'],
            total_tokens=prev_tokens['total_tokens'],
            estimated_cost=prev_tokens['estimated_cost'],
        )

        # 수동 입력 처리
        manual_input = request.POST.get('manual_input', '').strip()
        model_type = request.POST.get('model_type', '2.5-flash')
        valid_models = ['2.5-flash', '2.5-pro', 'flash', 'pro']

        if manual_input or model_type != '2.5-flash':
            execution.manual_input = manual_input
            execution.model_type = model_type if model_type in valid_models else '2.5-flash'
            execution.save()

        # 이미지 프롬프트 옵션: 한글금지 체크 시 텍스트 없는 프롬프트 생성
        if step_name == 'image_prompter':
            no_text = request.POST.get('no_text') == '1'
            if no_text:
                execution.intermediate_data = {'no_text': True}
                execution.save()

        # 인트로 영상 옵션: 씬 개수 선택
        if step_name == 'video_generator':
            scene_count = request.POST.get('scene_count', '4')
            try:
                scene_count = int(scene_count)
            except ValueError:
                scene_count = 4
            execution.intermediate_data = {'scene_count': scene_count}
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

            # 나머지는 비동기 실행 (시간이 걸림)
            thread = threading.Thread(target=service.run)
            thread.start()

            # AJAX 요청이면 JSON 응답
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': f'{step.display_name} 실행이 시작되었습니다.',
                    'execution_id': execution.pk,
                    'step_name': step.display_name,
                })

            # 일반 요청이면 진행률 페이지로 이동
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
def step_delete(request, pk, execution_id):
    """실행 기록 삭제 (running 제외)"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    execution = get_object_or_404(StepExecution, pk=execution_id, project=project)

    if execution.status == 'running':
        return JsonResponse({'success': False, 'message': '실행 중인 작업은 삭제할 수 없습니다.'})

    execution.delete()
    return JsonResponse({'success': True, 'message': '삭제되었습니다.'})


@login_required
@require_POST
def step_acknowledge(request, pk, execution_id):
    """완료된 작업 확인 처리 (목록에서 숨김)"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    execution = get_object_or_404(StepExecution, pk=execution_id, project=project)

    if execution.status == 'completed':
        execution.acknowledged = True
        execution.save()
        return JsonResponse({'success': True, 'message': '확인되었습니다.'})

    return JsonResponse({'success': False, 'message': '완료된 작업만 확인할 수 있습니다.'})


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

        # 이전 토큰 정보 가져오기 (누적)
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
        message = f'{step_names_display} 실행이 시작되었습니다.'

        # AJAX 요청이면 JSON 응답
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': message,
                'execution_ids': [e.pk for e in executions],
            })

        messages.info(request, message)
        return redirect('pipeline:project_data', pk=project.pk)
    else:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': '실행할 단계를 찾을 수 없습니다.',
            })
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

    # 모델 선택 가져오기
    model_settings = {
        'youtube_collector': None,  # 모델 사용 안함
        'transcript_analyzer': request.POST.get('model_transcript_analyzer', '2.5-flash'),
        'comment_analyzer': request.POST.get('model_comment_analyzer', '2.5-flash'),
        'script_planner': request.POST.get('model_script_planner', '2.5-flash'),
        'researcher': request.POST.get('model_researcher', '2.5-flash'),
        'script_writer': request.POST.get('model_script_writer', '2.5-pro'),
        'image_prompter': request.POST.get('model_image_prompter', '2.5-flash'),
    }

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
        model_type=request.POST.get('model_type', '2.5-pro'),
        input_tokens=prev_tokens['input_tokens'],
        output_tokens=prev_tokens['output_tokens'],
        total_tokens=prev_tokens['total_tokens'],
        estimated_cost=prev_tokens['estimated_cost'],
        intermediate_data={'model_settings': model_settings},
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

    # stale 상태 정리 (스레드 죽은 running 실행들)
    _cleanup_stale_executions(user=request.user)

    project = get_object_or_404(
        Project.objects.select_related('topic', 'research', 'draft'),
        pk=pk,
        user=request.user
    )

    # 실행 중 + 실패 + 완료(미확인) 작업들 (스텝별 최신만)
    running_executions = []
    seen_steps = set()
    for exec in project.step_executions.select_related('step').order_by('-created_at'):
        if exec.step_id not in seen_steps:
            seen_steps.add(exec.step_id)
            # running, failed는 항상 표시 / completed는 acknowledged=False일 때만
            if exec.status in ['running', 'failed']:
                running_executions.append(exec)
            elif exec.status == 'completed' and not exec.acknowledged:
                running_executions.append(exec)

    # 각 단계별 최근 실행 가져오기 (누적값 포함)
    steps = PipelineStep.objects.all()
    step_executions = {}
    total_tokens = 0
    total_cost = Decimal('0')

    for step in steps:
        execution = project.step_executions.filter(step=step).order_by('-created_at').first()
        step_executions[step.name] = execution
        if execution:
            # auto_pipeline은 하위 스텝 토큰을 복사한 것이므로 총계에서 제외 (중복 방지)
            if step.name != 'auto_pipeline':
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
def research_manual_notes(request, pk):
    """리서치 수동 자료 저장 API"""
    project = get_object_or_404(Project, pk=pk, user=request.user)

    manual_notes = request.POST.get('manual_notes', '').strip()
    topic = request.POST.get('topic', '').strip()

    # Research가 없으면 생성
    research, created = Research.objects.get_or_create(project=project)
    research.manual_notes = manual_notes
    if topic:
        research.topic = topic
    research.save()

    return JsonResponse({
        'success': True,
        'message': '수동 자료가 저장되었습니다.',
        'char_count': len(manual_notes),
    })


@login_required
@require_POST
def project_delete(request, pk):
    """프로젝트 삭제 (파일 포함)"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    name = project.name
    project.delete()  # 모델의 delete()에서 파일도 삭제

    # AJAX 요청이면 JSON 응답
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'success': True, 'message': f'프로젝트 "{name}"가 삭제되었습니다.'})

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
    """개별 씬 이미지 생성 (Gemini / Replicate 지원)"""
    import io
    import requests as http_requests
    from PIL import Image
    from google import genai
    from google.genai import types
    from django.core.files.base import ContentFile
    from apps.accounts.models import APIKey
    import replicate

    project = get_object_or_404(Project, pk=pk, user=request.user)
    scene = get_object_or_404(Scene, project=project, scene_number=scene_number)

    # POST에서 모델 타입 가져오기
    model_type = request.POST.get('model_type', 'pro')

    # 모델 설정 매핑
    MODEL_CONFIG = {
        'pro': {'provider': 'gemini', 'api_model': 'gemini-3-pro-image-preview'},
        'flash': {'provider': 'gemini', 'api_model': 'gemini-2.5-flash-image'},
        'flux': {'provider': 'replicate', 'api_model': 'black-forest-labs/flux-schnell'},
        'sdxl': {'provider': 'replicate', 'api_model': 'stability-ai/sdxl:7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc'},
    }

    config = MODEL_CONFIG.get(model_type, MODEL_CONFIG['pro'])
    provider = config['provider']
    api_model = config['api_model']

    # 프롬프트 구성 - 상황 묘사에 집중 (캐릭터/스타일은 이미지로 제공)
    base_prompt = scene.image_prompt or scene.narration or ''
    style = project.image_style
    character = project.character

    try:
        if provider == 'gemini':
            # Gemini API
            api_key = APIKey.objects.filter(user=request.user, service='gemini', is_default=True).first()
            if not api_key:
                return JsonResponse({'success': False, 'message': 'Gemini API 키가 없습니다.'})

            client = genai.Client(api_key=api_key.get_key())

            prompt = f"Generate an image based on this description:\n\n{base_prompt}\n\nAspect ratio: 16:9 (1920x1080), professional quality."
            contents = [prompt]

            # 스타일 샘플 이미지 추가
            style_added = 0
            if style:
                for sample in style.sample_images.all()[:3]:
                    try:
                        img = Image.open(sample.image.path)
                        contents.append(img)
                        style_added += 1
                    except:
                        pass
                if style_added > 0:
                    style_desc = style.style_prompt if style.style_prompt else "the reference images"
                    contents[0] = f"Use the reference images for background and artistic style. Style: {style_desc}\n\n{contents[0]}"

            # 캐릭터 이미지 추가
            if scene.has_character and character and character.image:
                try:
                    char_img = Image.open(character.image.path)
                    contents.append(char_img)
                    contents[0] = f"Include the character from the reference image.\n\n{contents[0]}"
                except:
                    pass

            response = client.models.generate_content(
                model=api_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT'],
                )
            )

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

                        return JsonResponse({'success': True, 'image_url': scene.image.url})

            return JsonResponse({'success': False, 'message': '이미지 생성 실패'})

        else:
            # Replicate API
            api_key = APIKey.objects.filter(user=request.user, service='replicate', is_default=True).first()
            if not api_key:
                api_key = APIKey.objects.filter(user=request.user, service='replicate').first()
            if not api_key:
                return JsonResponse({'success': False, 'message': 'Replicate API 키가 없습니다.'})

            prompt = f"{base_prompt}, 16:9 aspect ratio, professional quality, photorealistic"
            client = replicate.Client(api_token=api_key.get_key())

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
                        "width": 1344,
                        "height": 768,
                        "num_outputs": 1,
                        "scheduler": "K_EULER",
                        "num_inference_steps": 25,
                    }
                )
            else:
                output = client.run(api_model, input={"prompt": prompt, "num_outputs": 1})

            if output:
                image_url = output[0] if isinstance(output, list) else output
                if hasattr(image_url, 'url'):
                    image_url = image_url.url

                response = http_requests.get(str(image_url), timeout=30)
                response.raise_for_status()

                img = Image.open(io.BytesIO(response.content))
                img = img.resize((1920, 1080), Image.Resampling.LANCZOS)

                output_buffer = io.BytesIO()
                img.save(output_buffer, format='PNG')

                filename = f'scene_{scene_number:02d}.png'
                scene.image.save(filename, ContentFile(output_buffer.getvalue()), save=True)

                return JsonResponse({'success': True, 'image_url': scene.image.url})

            return JsonResponse({'success': False, 'message': 'Replicate 응답 없음'})

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

    # TTS용 텍스트 전처리 (Fish Speech가 처리 못하는 특수문자 제거)
    quote_chars = "'\u2018\u2019\u201a\u201b\"\u201c\u201d\u201e\u201f"
    for char in quote_chars:
        text = text.replace(char, "")
    text = re.sub(r'…+', '...', text)
    text = re.sub(r'\s+', ' ', text).strip()

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

    # narration_tts 직접 편집 (전달된 경우에만)
    if 'narration_tts' in request.POST and 'narration' not in request.POST:
        scene.narration_tts = request.POST.get('narration_tts', '').strip()
        updated_fields.append('narration_tts')

    # image_prompt 업데이트 (전달된 경우에만)
    if 'image_prompt' in request.POST:
        scene.image_prompt = request.POST.get('image_prompt', '').strip()
        updated_fields.append('image_prompt')

    # has_character 업데이트 (전달된 경우에만)
    if 'has_character' in request.POST:
        scene.has_character = request.POST.get('has_character') in ['true', 'True', '1', 'on']
        updated_fields.append('has_character')

    # regenerate_tts: 나레이션 변경 없이 TTS 텍스트만 재생성
    if 'regenerate_tts' in request.POST and 'narration' not in request.POST:
        if scene.narration:
            scene.narration_tts = convert_to_tts(scene.narration)
            if 'narration_tts' not in updated_fields:
                updated_fields.append('narration_tts')

    if updated_fields:
        scene.save(update_fields=updated_fields)

    return JsonResponse({
        'success': True,
        'message': '저장되었습니다.',
        'narration_tts': scene.narration_tts,
    })


@login_required
@require_POST
def scene_convert_tts(request, pk, scene_number):
    """개별 씬 TTS 텍스트 변환 (Gemini)"""
    from google import genai
    from apps.accounts.models import APIKey

    project = get_object_or_404(Project, pk=pk, user=request.user)
    scene = get_object_or_404(Scene, project=project, scene_number=scene_number)

    if not scene.narration:
        return JsonResponse({'success': False, 'message': '나레이션이 없습니다.'})

    model_type = request.POST.get('model_type', '2.5-flash')
    MODELS = {
        '2.5-flash': 'gemini-2.5-flash',
        '2.5-pro': 'gemini-2.5-pro',
        'flash': 'gemini-3-flash-preview',
        'pro': 'gemini-3-pro-preview',
    }
    model_name = MODELS.get(model_type, MODELS['2.5-flash'])

    api_key_obj = APIKey.objects.filter(user=request.user, service='gemini', is_default=True).first()
    if not api_key_obj:
        api_key_obj = APIKey.objects.filter(user=request.user, service='gemini').first()
    if not api_key_obj:
        return JsonResponse({'success': False, 'message': 'Gemini API 키가 없습니다.'})

    try:
        client = genai.Client(api_key=api_key_obj.get_key())

        prompt = f"""다음 텍스트를 TTS(음성 합성)용으로 변환해주세요.

## 핵심 규칙
**원본 띄어쓰기를 그대로 유지! 단어 개수가 절대 변하면 안 됨!**

## 변환 예시
- 원본에 띄어쓰기 없으면 → 변환 후에도 없음: "100달러" → "백달러", "1시간" → "한시간"
- 원본에 띄어쓰기 있으면 → 변환 후에도 있음: "100 달러" → "백 달러", "1 시간" → "한 시간"
- 연도: "2030년" → "이천삼십년"
- 금액: "2천만원" → "이천만원", "2천만 원" → "이천만 원"
- 퍼센트: "50%" → "오십퍼센트"
- 날짜: "1월1일" → "일월일일", "1월 1일" → "일월 일일"

## 금지사항
- 띄어쓰기 추가 금지
- 띄어쓰기 삭제 금지
- 단어 분리/병합 금지

## 원문
{scene.narration}

변환된 텍스트만 출력 (설명 없이):"""

        response = client.models.generate_content(model=model_name, contents=prompt)
        narration_tts = response.text.strip()

        scene.narration_tts = narration_tts
        scene.save(update_fields=['narration_tts'])

        return JsonResponse({'success': True, 'narration_tts': narration_tts})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)[:200]})


@login_required
@require_POST
def convert_all_tts(request, pk):
    """전체 씬 TTS 텍스트 변환 (Gemini)"""
    from google import genai
    from apps.accounts.models import APIKey
    import re as re_module

    project = get_object_or_404(Project, pk=pk, user=request.user)

    model_type = request.POST.get('model_type', '2.5-flash')
    MODELS = {
        '2.5-flash': 'gemini-2.5-flash',
        '2.5-pro': 'gemini-2.5-pro',
        'flash': 'gemini-3-flash-preview',
        'pro': 'gemini-3-pro-preview',
    }
    model_name = MODELS.get(model_type, MODELS['2.5-flash'])

    api_key_obj = APIKey.objects.filter(user=request.user, service='gemini', is_default=True).first()
    if not api_key_obj:
        api_key_obj = APIKey.objects.filter(user=request.user, service='gemini').first()
    if not api_key_obj:
        return JsonResponse({'success': False, 'message': 'Gemini API 키가 없습니다.'})

    scenes = list(project.scenes.filter(
        narration__isnull=False
    ).exclude(narration='').order_by('scene_number'))

    if not scenes:
        return JsonResponse({'success': False, 'message': '나레이션이 있는 씬이 없습니다.'})

    try:
        client = genai.Client(api_key=api_key_obj.get_key())

        results = []
        errors = 0
        batch_size = 10

        for i in range(0, len(scenes), batch_size):
            batch = scenes[i:i + batch_size]

            scenes_text = ""
            for scene in batch:
                scenes_text += f"[씬 {scene.scene_number}]\n{scene.narration}\n\n"

            prompt = f"""다음 텍스트들을 TTS(음성 합성)용으로 변환해주세요.

## 핵심 규칙
**원본 띄어쓰기를 그대로 유지! 단어 개수가 절대 변하면 안 됨!**

## 변환 예시
- 원본에 띄어쓰기 없으면 → 변환 후에도 없음: "100달러" → "백달러", "1시간" → "한시간"
- 원본에 띄어쓰기 있으면 → 변환 후에도 있음: "100 달러" → "백 달러", "1 시간" → "한 시간"
- 연도: "2030년" → "이천삼십년"
- 금액: "2천만원" → "이천만원", "2천만 원" → "이천만 원"
- 퍼센트: "50%" → "오십퍼센트"
- 날짜: "1월1일" → "일월일일", "1월 1일" → "일월 일일"

## 금지사항
- 띄어쓰기 추가 금지
- 띄어쓰기 삭제 금지
- 단어 분리/병합 금지

## 원문
{scenes_text}

## 출력 형식
[씬 N]
변환된 텍스트

[씬 N]
변환된 텍스트
..."""

            response = client.models.generate_content(model=model_name, contents=prompt)
            response_text = response.text.strip()

            # 파싱
            parts = re_module.split(r'\[씬\s*\d+\]\s*\n', response_text)
            parsed = []
            for part in parts[1:]:
                text = part.strip()
                if '\n\n' in text:
                    text = text.split('\n\n')[0]
                text = text.strip()
                if text:
                    parsed.append(text)

            for j, scene in enumerate(batch):
                if j < len(parsed):
                    scene.narration_tts = parsed[j]
                    scene.save(update_fields=['narration_tts'])
                    results.append({'scene': scene.scene_number, 'tts': parsed[j]})
                else:
                    errors += 1

        return JsonResponse({
            'success': True,
            'results': results,
            'converted': len(results),
            'errors': errors,
        })

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)[:200]})


@login_required
@require_POST
def delete_all_tts_text(request, pk):
    """모든 씬의 TTS 텍스트 삭제"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    count = project.scenes.exclude(narration_tts='').update(narration_tts='')
    return JsonResponse({'success': True, 'message': f'TTS 텍스트 {count}개 삭제됨'})


@login_required
@require_POST
def delete_all_image_prompts(request, pk):
    """모든 씬의 이미지 프롬프트 삭제"""
    project = get_object_or_404(Project, pk=pk, user=request.user)
    count = project.scenes.exclude(image_prompt='').update(image_prompt='')
    return JsonResponse({'success': True, 'message': f'이미지 프롬프트 {count}개 삭제됨'})


@login_required
@require_POST
def delete_mismatch_audio(request, pk):
    """자막 불일치 씬의 오디오 삭제"""
    import os

    project = get_object_or_404(Project, pk=pk, user=request.user)
    mismatch_scenes = project.scenes.filter(subtitle_status='mismatch')

    deleted_count = 0
    for scene in mismatch_scenes:
        if scene.audio:
            try:
                if os.path.exists(scene.audio.path):
                    os.remove(scene.audio.path)
            except Exception:
                pass
            scene.audio = ''
            scene.audio_duration = 0
        if scene.subtitle_file:
            try:
                if os.path.exists(scene.subtitle_file.path):
                    os.remove(scene.subtitle_file.path)
            except Exception:
                pass
            scene.subtitle_file = ''
        scene.subtitle_status = 'none'
        scene.subtitle_word_count = 0
        scene.save()
        deleted_count += 1

    return JsonResponse({'success': True, 'message': f'불일치 오디오 {deleted_count}개 삭제됨', 'count': deleted_count})


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
        has_audio = bool(scene.audio)
        has_subtitle = bool(scene.subtitle_file)

        if scene.audio:
            try:
                if os.path.exists(scene.audio.path):
                    os.remove(scene.audio.path)
            except Exception:
                pass
            scene.audio = None

        if scene.subtitle_file:
            try:
                if os.path.exists(scene.subtitle_file.path):
                    os.remove(scene.subtitle_file.path)
            except Exception:
                pass
            scene.subtitle_file = None

        if has_audio or has_subtitle:
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
    model_type = request.POST.get('model_type', '2.5-flash')
    MODELS = {
        '2.5-flash': 'gemini-2.5-flash',
        '2.5-pro': 'gemini-2.5-pro',
        'flash': 'gemini-3-flash-preview',
        'pro': 'gemini-3-pro-preview',
    }
    PRICING = {
        'gemini-2.5-flash': {'input': Decimal('0.30'), 'output': Decimal('2.50')},
        'gemini-2.5-pro': {'input': Decimal('1.25'), 'output': Decimal('10.00')},
        'gemini-3-flash-preview': {'input': Decimal('0.50'), 'output': Decimal('3.00')},
        'gemini-3-pro-preview': {'input': Decimal('2.00'), 'output': Decimal('12.00')},
    }
    model_name = MODELS.get(model_type, MODELS['2.5-flash'])

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

    # script_plan 가져오기
    script_plan = ''
    try:
        research = project.research
        if research and research.content_analysis:
            script_plan = research.content_analysis.get('script_plan', '')
    except Exception:
        pass

    # 토큰 사용량 추적용
    token_info = {'input': 0, 'output': 0, 'total': 0, 'cost': '0.0000'}

    # LLM으로 제목 + 설명 + 타임라인 생성
    try:
        # 사용자의 Gemini API 키 가져오기
        api_key_obj = APIKey.objects.filter(user=request.user, service='gemini', is_default=True).first()
        if not api_key_obj:
            api_key_obj = APIKey.objects.filter(user=request.user, service='gemini').first()
        if not api_key_obj:
            return JsonResponse({'success': False, 'message': 'Gemini API 키가 설정되지 않았습니다. 설정에서 API 키를 추가해주세요.'})
        api_key = api_key_obj.get_key()
        client = genai.Client(api_key=api_key)

        # 씬 정보를 텍스트로 변환 (시간 + 나레이션)
        scenes_text = ""
        for s in scene_info_list:
            mins = int(s['time'] // 60)
            secs = int(s['time'] % 60)
            scenes_text += f"[{mins}:{secs:02d}] 씬{s['scene']} ({s['section']}): {s['narration']}\n"

        total_mins = int(total_duration // 60)
        total_secs = int(total_duration % 60)

        # script_plan 섹션 추가
        script_plan_section = ""
        if script_plan:
            import json as json_mod
            script_plan_text = json_mod.dumps(script_plan, ensure_ascii=False, indent=2) if isinstance(script_plan, (dict, list)) else str(script_plan)
            script_plan_section = f"""
## 대본 생성 계획
{script_plan_text}
"""

        prompt = f"""YouTube 영상 업로드 정보를 생성해주세요.

## 영상 정보
- 총 길이: {total_mins}분 {total_secs}초
- 씬 개수: {len(scene_info_list)}개
{script_plan_section}
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
        # 사용자의 Gemini API 키 가져오기
        api_key_obj = APIKey.objects.filter(user=request.user, service='gemini', is_default=True).first()
        if not api_key_obj:
            api_key_obj = APIKey.objects.filter(user=request.user, service='gemini').first()
        if not api_key_obj:
            return JsonResponse({'success': False, 'message': 'Gemini API 키가 설정되지 않았습니다.'})
        client = genai.Client(api_key=api_key_obj.get_key())

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
        logger.info(f'[Thumbnail] Project {pk}: Gemini 이미지 생성 시작, model=gemini-3-pro-image-preview')
        response = client.models.generate_content(
            model='gemini-3-pro-image-preview',
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE', 'TEXT'],
            )
        )
        logger.info(f'[Thumbnail] Project {pk}: Gemini 응답 수신')

        # 이미지 추출
        if hasattr(response, 'candidates') and response.candidates:
            logger.info(f'[Thumbnail] Project {pk}: candidates={len(response.candidates)}, parts={len(response.candidates[0].content.parts)}')
            for i, part in enumerate(response.candidates[0].content.parts):
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                    logger.info(f'[Thumbnail] Project {pk}: 이미지 데이터 발견 (part {i}, size={len(image_data)} bytes)')
                    img = Image.open(io.BytesIO(image_data))
                    img = img.resize((1280, 720), Image.Resampling.LANCZOS)

                    output = io.BytesIO()
                    img.save(output, format='PNG')

                    project.thumbnail.save('thumbnail.png', ContentFile(output.getvalue()), save=True)
                    logger.info(f'[Thumbnail] Project {pk}: 썸네일 저장 완료')

                    return JsonResponse({
                        'success': True,
                        'thumbnail_url': project.thumbnail.url,
                    })
                else:
                    logger.info(f'[Thumbnail] Project {pk}: part {i} - inline_data 없음, type={type(part)}')
        else:
            logger.warning(f'[Thumbnail] Project {pk}: candidates 없음, response={response}')
            # 차단 이유 확인
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                block_reason = getattr(response.prompt_feedback, 'block_reason', None)
                if block_reason:
                    block_reasons = {
                        'SAFETY': '안전 정책 위반 (폭력/성인/혐오 등)',
                        'OTHER': '정책 위반 (실존 인물/저작권/정치인 등)',
                        'BLOCKLIST': '금지어 포함',
                        'PROHIBITED_CONTENT': '금지된 콘텐츠',
                    }
                    reason_str = str(block_reason).split('.')[-1] if '.' in str(block_reason) else str(block_reason)
                    reason_msg = block_reasons.get(reason_str, reason_str)
                    return JsonResponse({'success': False, 'message': f'Gemini 차단: {reason_msg}'})

        return JsonResponse({'success': False, 'message': '썸네일 생성 실패 - 이미지 없음'})

    except Exception as e:
        import traceback
        logger.error(f'[Thumbnail] Project {pk}: 에러 발생 - {type(e).__name__}: {str(e)}')
        logger.error(f'[Thumbnail] Project {pk}: traceback:\n{traceback.format_exc()}')
        return JsonResponse({'success': False, 'message': f'{type(e).__name__}: {str(e)[:100]}'})


# =============================================
# 사용자별 프롬프트 관리
# =============================================

@login_required
def user_prompt(request, agent_name):
    """사용자별 프롬프트 조회/저장 API"""
    from apps.prompts.models import AgentPrompt, UserAgentPrompt

    # 유효한 에이전트인지 확인
    valid_agents = dict(AgentPrompt.AGENT_CHOICES)
    if agent_name not in valid_agents:
        return JsonResponse({'success': False, 'message': f'잘못된 에이전트: {agent_name}'})

    if request.method == 'POST':
        # 저장
        content = request.POST.get('content', '').strip()
        if not content:
            return JsonResponse({'success': False, 'message': '프롬프트 내용을 입력해주세요.'})

        user_prompt_obj, created = UserAgentPrompt.objects.update_or_create(
            user=request.user,
            agent_name=agent_name,
            defaults={'prompt_content': content}
        )

        return JsonResponse({
            'success': True,
            'message': '저장되었습니다.',
            'created': created,
        })

    # GET - 조회
    # 1. 사용자 커스텀 프롬프트
    try:
        user_prompt_obj = UserAgentPrompt.objects.get(user=request.user, agent_name=agent_name)
        return JsonResponse({
            'success': True,
            'content': user_prompt_obj.prompt_content,
            'is_custom': True,
            'agent_name': agent_name,
            'display_name': valid_agents[agent_name],
        })
    except UserAgentPrompt.DoesNotExist:
        pass

    # 2. 시스템 기본 프롬프트
    try:
        system_prompt = AgentPrompt.objects.get(agent_name=agent_name, is_active=True)
        return JsonResponse({
            'success': True,
            'content': system_prompt.prompt_content,
            'is_custom': False,
            'agent_name': agent_name,
            'display_name': valid_agents[agent_name],
        })
    except AgentPrompt.DoesNotExist:
        pass

    # 3. 서비스 내장 기본 프롬프트
    default_content = _get_default_prompt(agent_name)
    return JsonResponse({
        'success': True,
        'content': default_content,
        'is_custom': False,
        'agent_name': agent_name,
        'display_name': valid_agents[agent_name],
    })


@login_required
@require_POST
def user_prompt_reset(request, agent_name):
    """사용자 프롬프트 초기화 (기본값으로 복원)"""
    from apps.prompts.models import UserAgentPrompt

    deleted, _ = UserAgentPrompt.objects.filter(
        user=request.user,
        agent_name=agent_name
    ).delete()

    return JsonResponse({
        'success': True,
        'message': '기본 프롬프트로 초기화되었습니다.' if deleted else '커스텀 프롬프트가 없습니다.',
        'deleted': deleted > 0,
    })


def _get_default_prompt(agent_name: str) -> str:
    """서비스 내장 기본 프롬프트 가져오기"""
    if agent_name == 'script_writer':
        from apps.pipeline.services.script_writer import ScriptWriterService
        return ScriptWriterService.DEFAULT_PROMPT
    elif agent_name == 'researcher':
        from apps.pipeline.services.researcher import ResearcherService
        return ResearcherService.DEFAULT_PROMPT
    elif agent_name == 'scene_planner':
        from apps.pipeline.services.scene_planner import ScenePlannerService
        return getattr(ScenePlannerService, 'DEFAULT_PROMPT', '')
    elif agent_name == 'image_prompter':
        from apps.pipeline.services.image_prompter import ImagePrompterService
        return getattr(ImagePrompterService, 'DEFAULT_PROMPT', '')
    elif agent_name == 'transcript_analyzer':
        from apps.pipeline.services.transcript_analyzer import TranscriptAnalyzerService
        return TranscriptAnalyzerService.DEFAULT_PROMPT
    elif agent_name == 'comment_analyzer':
        from apps.pipeline.services.comment_analyzer import CommentAnalyzerService
        return CommentAnalyzerService.DEFAULT_PROMPT
    elif agent_name == 'script_planner':
        from apps.pipeline.services.script_planner import ScriptPlannerService
        return ScriptPlannerService.DEFAULT_PROMPT
    return ''
