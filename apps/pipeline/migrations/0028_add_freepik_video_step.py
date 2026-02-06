from django.db import migrations


def create_freepik_video_step(apps, schema_editor):
    PipelineStep = apps.get_model('pipeline', 'PipelineStep')
    PipelineStep.objects.get_or_create(
        name='freepik_video',
        defaults={
            'display_name': '스톡 영상',
            'description': 'Freepik 스톡 영상 검색 및 다운로드',
            'order': 65,  # scene_generator(60)와 video_generator(70) 사이
        }
    )


def remove_freepik_video_step(apps, schema_editor):
    PipelineStep = apps.get_model('pipeline', 'PipelineStep')
    PipelineStep.objects.filter(name='freepik_video').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0027_project_freepik_interval_scene_stock_video_and_more'),
    ]

    operations = [
        migrations.RunPython(create_freepik_video_step, remove_freepik_video_step),
    ]
