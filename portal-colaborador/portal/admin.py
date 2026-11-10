from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from django.utils.html import format_html
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import Colaborador, EspelhoPonto, Holerite, LogAcesso, RegistroPonto

User = get_user_model()

admin.site.site_header = "Aqquer"
admin.site.site_title = "Aqquer"
admin.site.index_title = "Portal do Colaborador"

LIMITE_PDF = 5 * 1024 * 1024  # 5 MB


class HoleriteAdminForm(forms.ModelForm):
    class Meta:
        model = Holerite
        fields = "__all__"

    def clean_arquivo(self):
        arquivo = self.cleaned_data.get("arquivo")
        if isinstance(arquivo, UploadedFile):  # só valida quando há um upload novo
            if arquivo.size > LIMITE_PDF:
                raise ValidationError("O PDF deve ter no máximo 5 MB.")
            inicio = arquivo.read(5)
            arquivo.seek(0)
            if inicio != b"%PDF-":
                raise ValidationError("O arquivo enviado não é um PDF válido.")
        return arquivo


class ColaboradorInline(admin.StackedInline):
    model = Colaborador
    can_delete = False
    extra = 1  # para criar um usuário só de RH, deixe este bloco em branco


admin.site.unregister(User)


@admin.register(User)
class UsuarioAdmin(UserAdmin):
    inlines = [ColaboradorInline]


@admin.register(Colaborador)
class ColaboradorAdmin(admin.ModelAdmin):
    list_display = ("matricula", "usuario", "cargo", "setor", "deve_trocar_senha")
    list_filter = ("setor", "deve_trocar_senha")
    list_select_related = ("usuario",)
    search_fields = ("matricula", "cpf", "usuario__username", "usuario__first_name", "usuario__last_name")


class BuscaPorColaborador(admin.ModelAdmin):
    autocomplete_fields = ("colaborador",)
    list_select_related = ("colaborador__usuario",)
    search_fields = ("colaborador__matricula", "colaborador__cpf", "colaborador__usuario__first_name", "colaborador__usuario__last_name")


@admin.register(Holerite)
class HoleriteAdmin(BuscaPorColaborador):
    form = HoleriteAdminForm
    list_display = ("colaborador", "competencia", "tipo", "criado_em", "abrir")
    list_filter = ("tipo", "competencia")

    @admin.display(description="PDF")
    def abrir(self, obj):
        url = reverse("holerite_arquivo_rh", args=[obj.pk])
        return format_html('<a href="{}" target="_blank" rel="noopener">Abrir</a>', url)


@admin.register(RegistroPonto)
class RegistroPontoAdmin(BuscaPorColaborador):
    list_display = ("colaborador", "data", "batidas", "minutos_trabalhados", "minutos_extras", "falta")
    list_filter = ("falta", "data")


@admin.register(LogAcesso)
class LogAcessoAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "usuario", "acao", "detalhe", "ip")
    list_filter = ("acao",)
    search_fields = ("usuario__username", "detalhe", "ip")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EspelhoAdminForm(HoleriteAdminForm):
    class Meta:
        model = EspelhoPonto
        fields = "__all__"


@admin.register(EspelhoPonto)
class EspelhoPontoAdmin(BuscaPorColaborador):
    form = EspelhoAdminForm
    list_display = ("colaborador", "competencia", "criado_em", "abrir")
    list_filter = ("competencia",)
    date_hierarchy = "competencia"

    @admin.display(description="PDF")
    def abrir(self, obj):
        url = reverse("espelho_arquivo_rh", args=[obj.pk])
        return format_html('<a href="{}" target="_blank" rel="noopener">Abrir</a>', url)
