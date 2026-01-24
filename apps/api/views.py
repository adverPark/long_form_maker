from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from apps.pipeline.models import StepExecution


@login_required
def progress(request, execution_id):
    """단계 실행 진행률 API"""
    execution = get_object_or_404(
        StepExecution,
        pk=execution_id,
        project__user=request.user
    )

    data = {
        'status': execution.status,
        'progress_percent': execution.progress_percent,
        'progress_message': execution.progress_message,
        'error_message': execution.error_message if execution.status == 'failed' else '',
    }

    # 추가 정보 (output_data가 있는 경우)
    if execution.output_data:
        if 'current_scene' in execution.output_data:
            data['current_scene'] = execution.output_data['current_scene']
        if 'total_scenes' in execution.output_data:
            data['total_scenes'] = execution.output_data['total_scenes']

    return JsonResponse(data)


@login_required
def execution_detail(request, execution_id):
    """단계 실행 상세 정보 API"""
    execution = get_object_or_404(
        StepExecution,
        pk=execution_id,
        project__user=request.user
    )

    data = {
        'id': execution.pk,
        'step_name': execution.step.name,
        'step_display_name': execution.step.display_name,
        'status': execution.status,
        'progress_percent': execution.progress_percent,
        'progress_message': execution.progress_message,
        'error_message': execution.error_message,
        'started_at': execution.started_at.isoformat() if execution.started_at else None,
        'completed_at': execution.completed_at.isoformat() if execution.completed_at else None,
        'output_data': execution.output_data,
    }

    return JsonResponse(data)
