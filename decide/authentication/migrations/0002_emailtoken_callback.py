# Generated by Django 2.0 on 2020-12-28 16:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailtoken',
            name='callback',
            field=models.URLField(default='http://google.es', verbose_name='Callback'),
        ),
    ]
