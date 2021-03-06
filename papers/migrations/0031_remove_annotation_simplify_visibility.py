# -*- coding: utf-8 -*-
# Generated by Django 1.9.6 on 2016-06-11 09:23


from django.db import migrations, models

def init_visible(apps, schema_editor):
    Paper = apps.get_model('papers', 'Paper')

    for p in Paper.objects.all():
        p.update_visible()
        if not p.visible:
            p.save(update_fields=['visible'])

def do_nothing(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('papers', '0030_paper_researchers'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='annotation',
            name='paper',
        ),
        migrations.RemoveField(
            model_name='annotation',
            name='user',
        ),
        migrations.RemoveField(
            model_name='paper',
            name='visibility',
        ),
        migrations.AddField(
            model_name='paper',
            name='visible',
            field=models.BooleanField(default=True),
        ),
        migrations.DeleteModel(
            name='Annotation',
        ),
        migrations.RunPython(
            init_visible, do_nothing
        ),
    ]
