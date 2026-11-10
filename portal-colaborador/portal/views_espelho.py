"""Espelho de ponto em PDF: importação em lote (RH) e consulta pelo funcionário."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import importacao
from .models import Colaborador, EspelhoPonto
from .views import colaborador_required, registrar


def _resposta_pdf(e, baixar):
    try:
        arquivo = e.arquivo.open("rb")
    except FileNotFoundError:
        raise Http404("Arquivo não encontrado.")
    resposta = FileResponse(
        arquivo, content_type="application/pdf", as_attachment=baixar,
        filename=f"ponto_{e.competencia:%Y-%m}.pdf",
    )
    resposta["Cache-Control"] = "private, no-store"
    return resposta


# ---------- funcionário ----------

@colaborador_required
def espelhos_ponto(request, colab):
    lista = colab.espelhos_ponto.all()
    ano = request.GET.get("ano", "")
    if ano.isdigit():
        lista = lista.filter(competencia__year=int(ano))
    return render(request, "espelhos_ponto.html", {
        "espelhos": lista,
        "anos": [d.year for d in colab.espelhos_ponto.dates("competencia", "year", order="DESC")],
        "ano": ano,
    })


@colaborador_required
def espelho_arquivo(request, colab, pk):
    # Só o dono abre o próprio espelho. Para qualquer outra pessoa a resposta é 404
    # (igual à de um espelho inexistente) e a tentativa fica registrada no log.
    e = EspelhoPonto.objects.filter(pk=pk, colaborador=colab).first()
    if e is None:
        if EspelhoPonto.objects.filter(pk=pk).exists():
            registrar(request, "acesso_negado", f"espelho de ponto {pk}")
        raise Http404
    baixar = request.GET.get("baixar") == "1"
    registrar(request, "ponto_baixado" if baixar else "ponto_visualizado", str(e))
    return _resposta_pdf(e, baixar)


# ---------- RH ----------

@login_required
@permission_required("portal.view_espelhoponto", raise_exception=True)
def espelho_arquivo_rh(request, pk):
    e = get_object_or_404(EspelhoPonto.objects.select_related("colaborador__usuario"), pk=pk)
    registrar(request, "ponto_aberto_pelo_rh", str(e))
    return _resposta_pdf(e, baixar=False)


def _salvar_espelho(colab, competencia, dados):
    """Cria o espelho ou troca o PDF se já existir um daquele mês."""
    e = EspelhoPonto.objects.filter(colaborador=colab, competencia=competencia).first()
    criado = e is None
    if criado:
        e = EspelhoPonto(colaborador=colab, competencia=competencia)
    else:
        e.arquivo.delete(save=False)
    e.arquivo.save("ponto.pdf", ContentFile(dados), save=True)
    return criado


@login_required
@permission_required("portal.add_espelhoponto", raise_exception=True)
def espelhos_importar(request):
    contexto = {"mes_padrao": timezone.localdate().strftime("%Y-%m")}
    if request.method != "POST":
        return render(request, "espelhos_importar.html", contexto)

    try:
        ano, mes = map(int, request.POST.get("mes", "").split("-"))
        competencia = date(ano, mes, 1)
    except ValueError:
        messages.error(request, "Escolha o mês de referência.")
        return render(request, "espelhos_importar.html", contexto)
    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        messages.error(request, "Selecione ao menos um arquivo.")
        return render(request, "espelhos_importar.html", contexto)

    mapa = {c.cpf: c for c in Colaborador.objects.select_related("usuario")}
    resultados = []
    for nome, dados, erro in importacao.pdfs_do_upload(arquivos):
        colab = None
        if not erro:
            colab, erro = importacao.identificar(nome, dados, mapa)
        if erro:
            resultados.append({"arquivo": nome, "ok": False, "detalhe": erro})
            continue
        criado = _salvar_espelho(colab, competencia, dados)
        resultados.append({"arquivo": nome, "ok": True, "colab": colab,
                           "detalhe": "Importado" if criado else "Substituído"})

    ok = sum(1 for r in resultados if r["ok"])
    falhas = len(resultados) - ok
    registrar(request, "ponto_pdf_importado", f"{competencia:%m/%Y}: {ok} ok, {falhas} com erro")
    contexto.update({"processado": True, "resultados": resultados, "ok": ok, "falhas": falhas,
                     "competencia": competencia, "mes_padrao": competencia.strftime("%Y-%m")})
    return render(request, "espelhos_importar.html", contexto)
