from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.views.decorators.http import require_POST
from .models import APIKey, VoiceSample


def login_view(request):
    """로그인 페이지"""
    if request.user.is_authenticated:
        return redirect('pipeline:dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get('next', 'pipeline:dashboard')
            return redirect(next_url)
    else:
        form = AuthenticationForm()

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    """로그아웃"""
    logout(request)
    return redirect('accounts:login')


@login_required
def settings_view(request):
    """사용자 설정 페이지"""
    from .models import User

    api_keys = APIKey.objects.filter(user=request.user)
    voice_samples = VoiceSample.objects.filter(user=request.user)

    # API 키를 서비스별로 그룹화
    gemini_keys = api_keys.filter(service='gemini')
    replicate_keys = api_keys.filter(service='replicate')

    context = {
        'gemini_keys': gemini_keys,
        'replicate_keys': replicate_keys,
        'voice_samples': voice_samples,
        'services': APIKey.SERVICE_CHOICES,
        'gemini_model_choices': User.GEMINI_MODEL_CHOICES,
        'current_gemini_model': request.user.gemini_model,
    }
    return render(request, 'accounts/settings.html', context)


@login_required
@require_POST
def save_api_key(request):
    """API 키 저장"""
    service = request.POST.get('service')
    name = request.POST.get('name', '').strip()
    api_key_value = request.POST.get('api_key', '').strip()
    is_default = request.POST.get('is_default') == 'on'

    if not service or not api_key_value or not name:
        messages.error(request, '모든 필드를 입력해주세요.')
        return redirect('accounts:settings')

    # 첫 번째 키면 자동으로 기본 설정
    existing_count = APIKey.objects.filter(user=request.user, service=service).count()
    if existing_count == 0:
        is_default = True

    api_key = APIKey(
        user=request.user,
        service=service,
        name=name,
        is_default=is_default
    )
    api_key.set_key(api_key_value)
    api_key.save()

    messages.success(request, f'{name} API 키가 저장되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_api_key(request, pk):
    """API 키 삭제"""
    api_key = APIKey.objects.filter(user=request.user, pk=pk).first()
    if api_key:
        name = api_key.name
        was_default = api_key.is_default
        service = api_key.service
        api_key.delete()

        # 기본 키였으면 다른 키를 기본으로 설정
        if was_default:
            other_key = APIKey.objects.filter(user=request.user, service=service).first()
            if other_key:
                other_key.is_default = True
                other_key.save()

        messages.success(request, f'{name} API 키가 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_api_key(request, pk):
    """기본 API 키 설정"""
    api_key = APIKey.objects.filter(user=request.user, pk=pk).first()
    if api_key:
        api_key.is_default = True
        api_key.save()
        messages.success(request, f'{api_key.name}이(가) 기본 키로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def upload_voice(request):
    """목소리 샘플 업로드"""
    name = request.POST.get('name')
    audio_file = request.FILES.get('audio_file')

    if not name or not audio_file:
        messages.error(request, '이름과 파일을 모두 입력해주세요.')
        return redirect('accounts:settings')

    VoiceSample.objects.create(
        user=request.user,
        name=name,
        audio_file=audio_file,
        is_default=not VoiceSample.objects.filter(user=request.user).exists()
    )
    messages.success(request, '목소리 샘플이 업로드되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_voice(request, pk):
    """목소리 샘플 삭제"""
    VoiceSample.objects.filter(user=request.user, pk=pk).delete()
    messages.success(request, '목소리 샘플이 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_voice(request, pk):
    """기본 목소리 설정"""
    voice = VoiceSample.objects.filter(user=request.user, pk=pk).first()
    if voice:
        voice.is_default = True
        voice.save()
        messages.success(request, f'{voice.name}이(가) 기본 목소리로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_gemini_model(request):
    """Gemini 모델 설정"""
    model = request.POST.get('gemini_model')
    if model in ['flash', 'pro']:
        request.user.gemini_model = model
        request.user.save(update_fields=['gemini_model'])
        model_name = 'Gemini 3 Flash' if model == 'flash' else 'Gemini 3 Pro'
        messages.success(request, f'{model_name}으로 변경되었습니다.')
    return redirect('accounts:settings')
