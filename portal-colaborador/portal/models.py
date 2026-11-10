import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import RegexValidator
from django.db import models


class ArmazenamentoPrivado(FileSystemStorage):
    """Guarda os arquivos em PRIVATE_MEDIA_ROOT (lido a cada uso, o que facilita os testes)."""

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)


def formatar_minutos(minutos):
    sinal = "-" if minutos < 0 else ""
    horas, resto = divmod(abs(minutos), 60)
    return f"{sinal}{horas}h{resto:02d}"


class Colaborador(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="colaborador")
    matricula = models.CharField("matrícula", max_length=20, unique=True)
    cpf = models.CharField("CPF", max_length=11, unique=True, validators=[RegexValidator(r"^\d{11}$", "Informe 11 números, sem pontos ou traço.")])
    cargo = models.CharField(max_length=80, blank=True)
    setor = models.CharField(max_length=80, blank=True)
    deve_trocar_senha = models.BooleanField(
        "deve trocar a senha no próximo acesso", default=True,
        help_text="Marque novamente sempre que redefinir a senha pelo admin.",
    )

    class Meta:
        verbose_name_plural = "colaboradores"

    def __str__(self):
        return f"{self.usuario.get_full_name() or self.usuario.username} ({self.matricula})"


def caminho_holerite(instance, nome_original):
    # Nome aleatório: o caminho no disco não revela de quem é o documento nem é adivinhável.
    return f"holerites/{uuid.uuid4().hex}.pdf"


class Holerite(models.Model):
    class Tipo(models.TextChoices):
        MENSAL = "mensal", "Mensal"
        ADIANTAMENTO = "adiantamento", "Adiantamento"
        DECIMO = "13o", "13º salário"
        FERIAS = "ferias", "Férias"

    colaborador = models.ForeignKey(Colaborador, on_delete=models.CASCADE, related_name="holerites")
    competencia = models.DateField("competência", help_text="Use o dia 1 do mês de referência.")
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.MENSAL)
    arquivo = models.FileField(upload_to=caminho_holerite, storage=ArmazenamentoPrivado())
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-competencia", "tipo"]
        constraints = [models.UniqueConstraint(fields=["colaborador", "competencia", "tipo"], name="holerite_unico")]

    @property
    def ano(self):
        return self.competencia.year

    def __str__(self):
        return f"{self.colaborador.matricula} {self.competencia:%m/%Y} {self.get_tipo_display()}"


class RegistroPonto(models.Model):
    colaborador = models.ForeignKey(Colaborador, on_delete=models.CASCADE, related_name="pontos")
    data = models.DateField()
    batidas = models.CharField(max_length=100, blank=True, help_text="Horários separados por espaço. Ex.: 08:00 12:00 13:00 17:00")
    minutos_trabalhados = models.PositiveIntegerField(default=0)
    minutos_extras = models.PositiveIntegerField(default=0)
    falta = models.BooleanField(default=False)
    saldo_banco_minutos = models.IntegerField(default=0, help_text="Saldo acumulado ao fim do dia (pode ser negativo).")
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        ordering = ["data"]
        verbose_name = "registro de ponto"
        verbose_name_plural = "registros de ponto"
        constraints = [models.UniqueConstraint(fields=["colaborador", "data"], name="ponto_unico_por_dia")]

    @property
    def trabalhado(self):
        return formatar_minutos(self.minutos_trabalhados)

    @property
    def extras(self):
        return formatar_minutos(self.minutos_extras)

    def __str__(self):
        return f"{self.colaborador.matricula} {self.data:%d/%m/%Y}"


class LogAcesso(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    acao = models.CharField(max_length=50)
    detalhe = models.CharField(max_length=200, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "log de acesso"
        verbose_name_plural = "logs de acesso"


def caminho_espelho(instance, nome_original):
    return f"espelhos_ponto/{uuid.uuid4().hex}.pdf"


class EspelhoPonto(models.Model):
    """Espelho de ponto em PDF (um por funcionário e mês), no mesmo armazenamento privado dos holerites."""
    colaborador = models.ForeignKey(Colaborador, on_delete=models.CASCADE, related_name="espelhos_ponto")
    competencia = models.DateField("mês de referência")
    arquivo = models.FileField("PDF", upload_to=caminho_espelho, storage=ArmazenamentoPrivado())
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "espelho de ponto"
        verbose_name_plural = "espelhos de ponto"
        ordering = ["-competencia"]
        constraints = [models.UniqueConstraint(fields=["colaborador", "competencia"], name="espelho_unico_por_mes")]

    def __str__(self):
        return f"{self.colaborador} - ponto {self.competencia:%m/%Y}"
