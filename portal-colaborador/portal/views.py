import hashlib
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils import timezone

from . import importacao
from .models import Colaborador, Holerite, LogAcesso, formatar_minutos


def registrar(request, acao, detalhe="", usuario=None):
    usuario = usuario or (request.user if request.user.is_authenticated else None)
    LogAcesso.objects.create(
        usuario=usuario, acao=acao, detalhe=detalhe[:200], ip=request.META.get("REMOTE_ADDR") or None,
    )


def colaborador_required(view):
    """Exige login e um Colaborador vinculado; a view recebe o colaborador como 2º argumento."""
    @wraps(view)
    def interna(request, *args, **kwargs):
        colab = getattr(request.user, "colaborador", None)
        if colab is None:
            return render(request, "sem_vinculo.html", status=403)
        return view(request, colab, *args, **kwargs)
    return login_required(interna)


# ---------- autenticação ----------

def _chave_falhas(request):
    bruto = f"{request.POST.get('username', '').strip().lower()}|{request.META.get('REMOTE_ADDR', '')}"
    return "login-falhas:" + hashlib.sha256(bruto.encode()).hexdigest()


class LoginView(auth_views.LoginView):
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if cache.get(_chave_falhas(request), 0) >= settings.LOGIN_MAX_TENTATIVAS:
            registrar(request, "login_bloqueado", request.POST.get("username", ""))
            return self.render_to_response(self.get_context_data(form=self.get_form(), bloqueado=True))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        chave = _chave_falhas(self.request)
        cache.set(chave, cache.get(chave, 0) + 1, settings.LOGIN_BLOQUEIO_SEGUNDOS)
        registrar(self.request, "login_falhou", self.request.POST.get("username", ""))
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_chave_falhas(self.request))
        registrar(self.request, "login", usuario=form.get_user())
        return super().form_valid(form)


class TrocarSenhaView(auth_views.PasswordChangeView):
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        resposta = super().form_valid(form)
        Colaborador.objects.filter(usuario=self.request.user).update(deve_trocar_senha=False)
        registrar(self.request, "senha_alterada")
        messages.success(self.request, "Senha alterada.")
        return resposta


class RedefinirSenhaView(auth_views.PasswordResetConfirmView):
    def form_valid(self, form):
        resposta = super().form_valid(form)
        Colaborador.objects.filter(usuario=form.user).update(deve_trocar_senha=False)
        return resposta


# ---------- telas do colaborador ----------

@colaborador_required
def home(request, colab):
    return render(request, "home.html", {
        "colab": colab,
        "ultimo": colab.holerites.first(),
        "mes_atual": timezone.localdate().strftime("%Y-%m"),
    })


@colaborador_required
def holerites(request, colab):
    lista = colab.holerites.all()
    ano = request.GET.get("ano", "")
    tipo = request.GET.get("tipo", "")
    if ano.isdigit():
        lista = lista.filter(competencia__year=int(ano))
    if tipo in Holerite.Tipo.values:
        lista = lista.filter(tipo=tipo)
    return render(request, "holerites.html", {
        "holerites": lista,
        "anos": [d.year for d in colab.holerites.dates("competencia", "year", order="DESC")],
        "ano": ano,
        "tipo": tipo,
        "tipos": Holerite.Tipo.choices,
    })


def _resposta_pdf(h, baixar):
    try:
        arquivo = h.arquivo.open("rb")
    except FileNotFoundError:
        raise Http404("Arquivo não encontrado.")
    resposta = FileResponse(
        arquivo, content_type="application/pdf", as_attachment=baixar,
        filename=f"holerite_{h.competencia:%Y-%m}_{h.tipo}.pdf",
    )
    resposta["Cache-Control"] = "private, no-store"
    return resposta


@colaborador_required
def holerite_arquivo(request, colab, pk):
    # Regra de acesso: só o dono abre o próprio holerite. Para qualquer outra pessoa a resposta
    # é 404 (igual à de um holerite inexistente) e a tentativa fica registrada no log.
    h = Holerite.objects.filter(pk=pk, colaborador=colab).first()
    if h is None:
        if Holerite.objects.filter(pk=pk).exists():
            registrar(request, "acesso_negado", f"holerite {pk}")
        raise Http404
    baixar = request.GET.get("baixar") == "1"
    registrar(request, "holerite_baixado" if baixar else "holerite_visualizado", str(h))
    return _resposta_pdf(h, baixar)


@login_required
@permission_required("portal.view_holerite", raise_exception=True)
def holerite_arquivo_rh(request, pk):
    """Acesso do RH: exige a permissão 'ver holerite' e fica registrado no log."""
    h = get_object_or_404(Holerite.objects.select_related("colaborador__usuario"), pk=pk)
    registrar(request, "holerite_aberto_pelo_rh", str(h))
    return _resposta_pdf(h, baixar=False)


@colaborador_required
def ponto(request, colab):
    hoje = timezone.localdate()
    try:
        ano, mes = map(int, request.GET.get("mes", "").split("-"))
        inicio = date(ano, mes, 1)
    except ValueError:
        inicio = hoje.replace(day=1)

    registros = colab.pontos.filter(data__year=inicio.year, data__month=inicio.month)
    total = registros.aggregate(
        trabalhado=Sum("minutos_trabalhados"), extras=Sum("minutos_extras"),
        faltas=Count("id", filter=Q(falta=True)),
    )
    ultimo = registros.last()
    proximo = (inicio + timedelta(days=32)).replace(day=1)
    return render(request, "ponto.html", {
        "inicio": inicio,
        "anterior": (inicio - timedelta(days=1)).replace(day=1),
        "proximo": proximo,
        "tem_proximo": proximo <= hoje,
        "registros": registros,
        "total_trabalhado": formatar_minutos(total["trabalhado"] or 0),
        "total_extras": formatar_minutos(total["extras"] or 0),
        "faltas": total["faltas"],
        "saldo": formatar_minutos(ultimo.saldo_banco_minutos if ultimo else 0),
    })


# ---------- importação em lote de holerites (RH) ----------

def _salvar_holerite(colab, competencia, tipo, dados):
    """Cria o holerite ou troca o PDF se já existir um do mesmo mês e tipo."""
    h = Holerite.objects.filter(colaborador=colab, competencia=competencia, tipo=tipo).first()
    criado = h is None
    if criado:
        h = Holerite(colaborador=colab, competencia=competencia, tipo=tipo)
    else:
        h.arquivo.delete(save=False)
    h.arquivo.save("holerite.pdf", ContentFile(dados), save=True)
    return criado


@login_required
@permission_required("portal.add_holerite", raise_exception=True)
def holerites_importar(request):
    contexto = {"tipos": Holerite.Tipo.choices, "mes_padrao": timezone.localdate().strftime("%Y-%m")}
    if request.method != "POST":
        return render(request, "holerites_importar.html", contexto)

    try:
        ano, mes = map(int, request.POST.get("mes", "").split("-"))
        competencia = date(ano, mes, 1)
    except ValueError:
        messages.error(request, "Escolha o mês de referência.")
        return render(request, "holerites_importar.html", contexto)
    tipo = request.POST.get("tipo", "")
    arquivos = request.FILES.getlist("arquivos")
    if tipo not in Holerite.Tipo.values or not arquivos:
        messages.error(request, "Escolha o tipo e selecione ao menos um arquivo.")
        return render(request, "holerites_importar.html", contexto)

    mapa = {c.cpf: c for c in Colaborador.objects.select_related("usuario")}
    resultados = []
    for nome, dados, erro in importacao.pdfs_do_upload(arquivos):
        colab = None
        if not erro:
            colab, erro = importacao.identificar(nome, dados, mapa)
        if erro:
            resultados.append({"arquivo": nome, "ok": False, "detalhe": erro})
            continue
        criado = _salvar_holerite(colab, competencia, tipo, dados)
        resultados.append({"arquivo": nome, "ok": True, "colab": colab,
                           "detalhe": "Importado" if criado else "Substituído"})

    ok = sum(1 for r in resultados if r["ok"])
    falhas = len(resultados) - ok
    registrar(request, "holerites_importados", f"{competencia:%m/%Y} {tipo}: {ok} ok, {falhas} com erro")
    contexto.update({"processado": True, "resultados": resultados, "ok": ok, "falhas": falhas,
                     "competencia": competencia, "mes_padrao": competencia.strftime("%Y-%m")})
    return render(request, "holerites_importar.html", contexto)
