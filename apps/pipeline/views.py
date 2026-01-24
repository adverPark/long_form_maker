import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.http import require_POST
from .models import Project, PipelineStep, StepExecution, Topic, Research, Draft, Scene
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
        # 실행 생성
        execution = StepExecution.objects.create(
            project=project,
            step=step
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
def project_data(request, pk):
    """프로젝트 데이터 보기 (Topic, Research, Draft, Scenes)"""
    project = get_object_or_404(
        Project.objects.select_related('topic', 'research', 'draft'),
        pk=pk,
        user=request.user
    )

    # 각 단계별 최근 실행 가져오기
    steps = PipelineStep.objects.all()
    step_executions = {}
    for step in steps:
        execution = project.step_executions.filter(step=step).order_by('-created_at').first()
        step_executions[step.name] = execution

    context = {
        'project': project,
        'topic': getattr(project, 'topic', None),
        'research': getattr(project, 'research', None),
        'draft': getattr(project, 'draft', None),
        'scenes': project.scenes.all(),
        'steps': steps,
        'step_executions': step_executions,
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
