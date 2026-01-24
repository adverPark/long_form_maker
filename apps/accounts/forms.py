from django import forms
from .models import APIKey, VoiceSample


class APIKeyForm(forms.ModelForm):
    api_key = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'API 키 입력'}),
        label='API 키'
    )

    class Meta:
        model = APIKey
        fields = ['service']
        widgets = {
            'service': forms.Select(attrs={'class': 'form-select'})
        }


class VoiceSampleForm(forms.ModelForm):
    class Meta:
        model = VoiceSample
        fields = ['name', 'description', 'audio_file']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '목소리 이름'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'audio_file': forms.FileInput(attrs={'class': 'form-control', 'accept': 'audio/*'}),
        }
