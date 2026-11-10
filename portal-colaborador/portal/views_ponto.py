"""Importação em lote de registros de ponto (RH): envia o relatório, confere a prévia e confirma."""
import secrets
from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render

from . import importacao_ponto as ip
from .models import Colaborador, RegistroPonto
from .views import registrar

MAX_ERROS_NA_TELA = 200

# coluna do relatório -> campo do modelo RegistroPonto
CAMPOS = {
    "batidas": "batidas",
    "trabalhado": "minutos_trabalhados",
    "extras": "minutos_extras",
    "falta": "falta",
    "saldo": "saldo_banco_minutos",
    "observacao": "observacao",
}


def _campos_gravaveis():
    """Só grava em campos que existem de verdade no modelo (e não são propriedades calculadas)."""
    nomes = {f.name for f in RegistroPonto._meta.concrete_fields}
    return {chave: campo for chave, campo in CAMPOS.items() if campo in nomes}


@login_required
@permission_required("portal.add_registroponto", raise_exception=True)
def ponto_modelo(request):
    """Planilha de exemplo para baixar e preencher/exportar do sistema do relógio."""
    linhas = [
        "CPF;Data;Entrada 1;Saída 1;Entrada 2;Saída 2;Horas trabalhadas;Horas extras;Falta;Saldo banco de horas;Observação",
        "123.456.789-01;01/09/2026;08:00;12:00;13:00;17:00;08:00;00:00;Não;00:00;",
        "123.456.789-01;02/09/2026;08:00;12:00;13:00;18:30;09:30;01:30;Não;01:30;",
        "123.456.789-01;03/09/2026;;;;;00:00;00:00;Sim;-08:00;Atestado",
    ]
    resposta = HttpResponse("\ufeff" + "\r\n".join(linhas) + "\r\n", content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = 'attachment; filename="modelo_ponto.csv"'
    return resposta


def _mapas():
    mapa_cpf, mapa_matricula = {}, {}
    for c in Colaborador.objects.select_related("usuario"):
        mapa_cpf[c.cpf] = c
        mapa_matricula[ip.converter_matricula(c.matricula)] = c
    return mapa_cpf, mapa_matricula


def _aplicar(itens, gravar):
    """Compara os itens com o banco e, se gravar=True, cria/atualiza.

    itens: [{"c": id do colaborador, "d": "AAAA-MM-DD", "v": {campo: valor}}]
    Retorna (resumo por colaborador, total de novos, total de atualizados).
    """
    colabs = Colaborador.objects.select_related("usuario").in_bulk({i["c"] for i in itens})
    datas = [date.fromisoformat(i["d"]) for i in itens]
    existentes = {}
    if itens:
        existentes = {
            (r.colaborador_id, r.data): r
            for r in RegistroPonto.objects.filter(
                colaborador_id__in=colabs.keys(), data__gte=min(datas), data__lte=max(datas))
        }
    resumo = defaultdict(lambda: {"novos": 0, "atualizados": 0})
    novos, alterados, campos = [], [], set()
    for item, dia in zip(itens, datas):
        colab = colabs.get(item["c"])
        if colab is None:          # removido entre a prévia e a confirmação
            continue
        atual = existentes.get((colab.pk, dia))
        if atual is None:
            novos.append(RegistroPonto(colaborador=colab, data=dia, **item["v"]))
            resumo[colab]["novos"] += 1
        else:
            for campo, valor in item["v"].items():
                setattr(atual, campo, valor)
            campos |= set(item["v"])
            alterados.append(atual)
            resumo[colab]["atualizados"] += 1
    if gravar:
        with transaction.atomic():
            if novos:
                RegistroPonto.objects.bulk_create(novos, batch_size=500)
            if alterados and campos:
                RegistroPonto.objects.bulk_update(alterados, sorted(campos), batch_size=500)
    return sorted(resumo.items(), key=lambda x: str(x[0])), len(novos), len(alterados)


@login_required
@permission_required("portal.add_registroponto", raise_exception=True)
def ponto_importar(request):
    if request.method != "POST":
        return render(request, "ponto_importar.html", {})
    if request.POST.get("confirmar"):
        return _confirmar(request)
    return _previa(request)


def _previa(request):
    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        messages.error(request, "Selecione o relatório de ponto (CSV ou XLSX).")
        return render(request, "ponto_importar.html", {})

    mapa_cpf, mapa_matricula = _mapas()
    erros, arquivos_ruins, validos, colunas_vistas = [], [], {}, set()
    for arq in arquivos:
        nome = arq.name.replace("\\", "/").split("/")[-1]
        if arq.size > ip.LIMITE_ARQUIVO:
            arquivos_ruins.append({"arquivo": nome, "detalhe": "maior que 5 MB"})
            continue
        try:
            relatorio = ip.ler_relatorio(nome, arq.read())
        except ip.ErroRelatorio as e:
            arquivos_ruins.append({"arquivo": nome, "detalhe": str(e)})
            continue
        colunas_vistas |= relatorio["colunas"]
        for e in relatorio["erros"]:
            erros.append({"arquivo": nome, "linha": e["linha"], "detalhe": e["detalhe"]})
        for item in relatorio["linhas"]:
            colab, motivo = ip.achar_colaborador(item, mapa_cpf, mapa_matricula)
            if motivo:
                erros.append({"arquivo": nome, "linha": item["linha"], "detalhe": motivo})
                continue
            chave = (colab.pk, item["data"])
            if chave in validos:
                erros.append({"arquivo": nome, "linha": item["linha"],
                              "detalhe": f"{colab} já tem registro em {item['data']:%d/%m/%Y} neste envio"})
                continue
            validos[chave] = (colab, item)

    gravaveis = _campos_gravaveis()
    ignoradas = sorted(c for c in colunas_vistas if c in CAMPOS and c not in gravaveis)
    itens = [
        {"c": colab.pk, "d": item["data"].isoformat(),
         "v": {gravaveis[k]: item[k] for k in gravaveis if k in item}}
        for colab, item in validos.values()
    ]
    resumo, n_novos, n_atualizados = _aplicar(itens, gravar=False)

    erros.sort(key=lambda e: (e["arquivo"], e["linha"]))
    falhas = len(erros) + len(arquivos_ruins)
    contexto = {
        "estado": "previa", "ok": len(itens), "falhas": falhas,
        "novos": n_novos, "atualizados": n_atualizados, "resumo": resumo,
        "arquivos_ruins": arquivos_ruins, "erros": erros[:MAX_ERROS_NA_TELA],
        "erros_extras": max(0, len(erros) - MAX_ERROS_NA_TELA), "ignoradas": ignoradas,
    }
    if itens:
        token = secrets.token_urlsafe(16)
        request.session["ponto_importacao"] = {
            "token": token, "itens": itens, "falhas": falhas, "ignoradas": ignoradas,
            "arquivos_ruins": arquivos_ruins, "erros": contexto["erros"], "erros_extras": contexto["erros_extras"],
        }
        contexto["token"] = token
    return render(request, "ponto_importar.html", contexto)


def _confirmar(request):
    pendente = request.session.get("ponto_importacao")
    token = request.POST.get("token", "")
    if not pendente or not secrets.compare_digest(str(pendente["token"]), token):
        messages.error(request, "A prévia expirou. Envie o relatório de novo.")
        return render(request, "ponto_importar.html", {})

    resumo, n_novos, n_atualizados = _aplicar(pendente["itens"], gravar=True)
    del request.session["ponto_importacao"]
    ok = n_novos + n_atualizados
    registrar(request, "ponto_importado", f"{ok} dia(s) ok, {pendente['falhas']} com erro")
    return render(request, "ponto_importar.html", {
        "estado": "gravado", "ok": ok, "falhas": pendente["falhas"], "resumo": resumo,
        "arquivos_ruins": pendente["arquivos_ruins"], "erros": pendente["erros"],
        "erros_extras": pendente["erros_extras"], "ignoradas": pendente["ignoradas"],
    })
