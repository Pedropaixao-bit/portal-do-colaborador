import django.db.models.deletion
import portal.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='EspelhoPonto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('competencia', models.DateField(verbose_name='mês de referência')),
                ('arquivo', models.FileField(storage=portal.models.ArmazenamentoPrivado(), upload_to=portal.models.caminho_espelho, verbose_name='PDF')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('colaborador', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='espelhos_ponto', to='portal.colaborador')),
            ],
            options={
                'verbose_name': 'espelho de ponto',
                'verbose_name_plural': 'espelhos de ponto',
                'ordering': ['-competencia'],
                'constraints': [models.UniqueConstraint(fields=('colaborador', 'competencia'), name='espelho_unico_por_mes')],
            },
        ),
    ]
