"""Leitura do relatório de ponto eletrônico enviado em lote (CSV ou XLSX), sem depender do Django.

O relatório precisa ter uma linha de cabeçalho. As colunas são reconhecidas pelo nome
(sem diferenciar maiúsculas, acentos ou pontuação). Só são obrigatórias:
  - CPF (ou Matrícula), para saber de quem é a linha;
  - Data.
As demais são opcionais: horas trabalhadas, horas extras, falta, saldo do banco de horas
e observação.
"""
import csv
import io
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta

LIMITE_ARQUIVO = 5 * 1024 * 1024   # 5 MB por relatório
LIMITE_LINHAS = 100_000            # linhas de dados por relatório
LINHAS_PARA_ACHAR_CABECALHO = 30   # relatórios de relógio costumam ter título antes da tabela
LIMITE_OBSERVACAO = 200            # tamanho do campo no banco (RegistroPonto.observacao)
LIMITE_BATIDAS = 100               # tamanho do campo no banco (RegistroPonto.batidas)

# nome interno -> nomes aceitos no cabeçalho (já normalizados, ver _normalizar)
COLUNAS = {
    "cpf": {"cpf", "cpf do funcionario", "cpf funcionario", "cpf colaborador"},
    "matricula": {"matricula", "matric", "chapa", "mat"},
    "data": {"data", "dia", "data do ponto", "data ponto", "data do registro"},
    "trabalhado": {
        "horas trabalhadas", "hs trabalhadas", "trabalhadas", "trabalhado", "horas trabalhado",
        "total trabalhado", "total de horas", "horas",
    },
    "extras": {"horas extras", "hs extras", "extras", "extra", "horas extra", "he"},
    "falta": {"falta", "faltas", "ausencia", "faltou"},
    "saldo": {
        "saldo banco de horas", "saldo do banco de horas", "saldo banco", "banco de horas",
        "saldo", "saldo bh", "saldo banco horas", "saldo do banco", "saldo banco de horas h",
    },
    "observacao": {"observacao", "observacoes", "obs", "justificativa", "ocorrencia"},
}

_SIM = {"sim", "s", "1", "x", "true", "verdadeiro", "v", "falta", "faltou"}
_NAO = {"", "nao", "n", "0", "false", "falso", "f", "-"}

# colunas de horário de entrada/saída: "Entrada", "Saída 2", "Batidas"...
_COLUNA_BATIDA = re.compile(r"^(batidas?|entrada|saida|ent|sai)( ?\d{1,2})?$")
_HORA = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?::[0-5]\d)?(?!\d)")
_SEM_BATIDA = {"falta", "folga", "dsr", "atestado", "feriado", "ferias", "abono", "compensado"}

_HORAS = re.compile(r"^([+-]?)\s*(\d{1,4}):([0-5]\d)(?::[0-5]\d)?$")
_DATA_BR = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
_DATA_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


class ErroRelatorio(Exception):
    """Problema no arquivo inteiro (não numa linha só)."""


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _vazio(valor):
    return valor is None or (isinstance(valor, str) and not valor.strip())


# ---------- conversão de valores ----------

def converter_cpf(valor):
    """Devolve só os 11 dígitos. Número vindo do Excel perde o zero da frente, então completa."""
    if isinstance(valor, bool):
        raise ValueError("CPF inválido")
    if isinstance(valor, (int, float)):
        digitos = str(int(valor)).zfill(11)
    else:
        digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) != 11:
        raise ValueError(f"CPF inválido: {str(valor).strip()[:20]!r} (precisa ter 11 números)")
    return digitos


def converter_matricula(valor):
    """Compara matrículas sem zeros à esquerda e sem diferença de maiúsculas (o Excel perde os zeros)."""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor).strip().upper()
    return texto.lstrip("0") or ("0" if texto else "")


def converter_data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    m = _DATA_ISO.search(texto)
    if m:
        ano, mes, dia = map(int, m.groups())
    else:
        m = _DATA_BR.search(texto)
        if not m:
            raise ValueError(f"data inválida: {texto[:20]!r} (use DD/MM/AAAA)")
        dia, mes, ano = map(int, m.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        raise ValueError(f"data inexistente: {texto[:20]!r}")


def converter_minutos(valor, permite_negativo=False):
    """'08:30' -> 510. Aceita também hora/duração do Excel. Vazio vale 0."""
    if _vazio(valor):
        return 0
    if isinstance(valor, timedelta):
        total = int(valor.total_seconds() // 60)
    elif isinstance(valor, datetime):
        total = valor.hour * 60 + valor.minute
    elif isinstance(valor, time):
        total = valor.hour * 60 + valor.minute
    elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
        raise ValueError(f"horas inválidas: {valor!r} (use o formato HH:MM, por exemplo 08:30)")
    else:
        m = _HORAS.match(str(valor).strip())
        if not m:
            raise ValueError(f"horas inválidas: {str(valor).strip()[:20]!r} (use o formato HH:MM, por exemplo 08:30)")
        sinal, horas, minutos = m.groups()
        total = int(horas) * 60 + int(minutos)
        if sinal == "-":
            total = -total
    if total < 0 and not permite_negativo:
        raise ValueError(f"horas não podem ser negativas: {str(valor).strip()[:20]!r}")
    return total


def converter_falta(valor):
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    texto = _normalizar(valor)
    if texto in _SIM:
        return True
    if texto in _NAO:
        return False
    raise ValueError(f"valor inválido na coluna Falta: {str(valor).strip()[:20]!r} (use Sim ou Não)")


# ---------- leitura do arquivo ----------

def _linhas_csv(dados):
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = dados.decode("cp1252", errors="replace")
    amostra = "\n".join(texto.splitlines()[:LINHAS_PARA_ACHAR_CABECALHO])
    separador = max(";,\t|", key=amostra.count)
    return [list(l) for l in csv.reader(io.StringIO(texto), delimiter=separador)]


def _linhas_xlsx(dados):
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ErroRelatorio("a leitura de planilhas Excel não está instalada (openpyxl); envie em CSV")
    try:
        wb = load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
    except Exception:
        raise ErroRelatorio("não foi possível abrir a planilha (arquivo corrompido ou protegido por senha)")
    try:
        ws = wb.worksheets[0]
        return [list(l) for l in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _achar_cabecalho(linhas):
    """Procura, nas primeiras linhas, a que tem as colunas de identificação e de data."""
    for i, linha in enumerate(linhas[:LINHAS_PARA_ACHAR_CABECALHO]):
        mapa, batidas = {}, []
        for j, celula in enumerate(linha):
            nome = _normalizar(celula)
            for chave, aceitos in COLUNAS.items():
                if nome in aceitos and chave not in mapa:
                    mapa[chave] = j
            if _COLUNA_BATIDA.match(nome):
                batidas.append(j)
        if batidas:
            mapa["batidas"] = batidas
        if "data" in mapa and ("cpf" in mapa or "matricula" in mapa):
            return i, mapa
    raise ErroRelatorio(
        "não encontrei o cabeçalho. O relatório precisa ter uma coluna CPF (ou Matrícula) e uma coluna Data"
    )


def _batidas(linha, colunas):
    """Junta os horários das colunas de entrada/saída em '08:00 12:00 13:00 17:00'."""
    horarios = []
    for j in colunas:
        v = linha[j] if j < len(linha) else None
        if _vazio(v):
            continue
        if isinstance(v, (datetime, time)):
            horarios.append(f"{v.hour:02d}:{v.minute:02d}")
        elif isinstance(v, timedelta):
            total = int(v.total_seconds() // 60)
            horarios.append(f"{total // 60:02d}:{total % 60:02d}")
        else:
            achados = _HORA.findall(str(v))
            if achados:
                horarios += [f"{int(h):02d}:{m}" for h, m in achados]
            elif _normalizar(v) in _SEM_BATIDA or not re.search(r"[0-9a-zA-Z]", str(v)):
                continue          # "--:--", "-", "Folga"...: dia sem batida
            else:
                raise ValueError(f"horário inválido: {str(v).strip()[:20]!r} (use HH:MM)")
    return " ".join(horarios)[:LIMITE_BATIDAS]


def ler_relatorio(nome_arquivo, dados):
    """Lê um relatório de ponto.

    Retorna {"colunas": {...}, "linhas": [...], "erros": [...]}. Cada linha válida é um dict com
    "linha" (número no arquivo), "data", e "cpf" e/ou "matricula", mais só as colunas que existem
    no relatório: "trabalhado", "extras", "falta", "saldo", "observacao" (horas em minutos).
    Levanta ErroRelatorio se o arquivo inteiro não serve.
    """
    if len(dados) > LIMITE_ARQUIVO:
        raise ErroRelatorio("maior que 5 MB")
    ext = os.path.splitext(nome_arquivo)[1].lower()
    if ext == ".xlsx":
        if not dados.startswith(b"PK"):
            raise ErroRelatorio("não é uma planilha .xlsx válida")
        linhas = _linhas_xlsx(dados)
    elif ext in (".csv", ".txt"):
        if b"\x00" in dados[:2048]:
            raise ErroRelatorio("não é um arquivo de texto/CSV válido")
        linhas = _linhas_csv(dados)
    else:
        raise ErroRelatorio("formato não aceito (envie CSV ou XLSX)")

    inicio, mapa = _achar_cabecalho(linhas)
    corpo = linhas[inicio + 1:]
    if len(corpo) > LIMITE_LINHAS:
        raise ErroRelatorio(f"mais de {LIMITE_LINHAS} linhas; divida o relatório em partes")

    def celula(linha, chave):
        j = mapa.get(chave)
        return linha[j] if j is not None and j < len(linha) else None

    resultado, erros = [], []
    for k, linha in enumerate(corpo, start=inicio + 2):
        if all(_vazio(c) for c in linha):
            continue
        # linhas de total/rodapé não têm data: só ignora se também não tiverem CPF/matrícula
        if _vazio(celula(linha, "data")) and _vazio(celula(linha, "cpf")) and _vazio(celula(linha, "matricula")):
            continue
        try:
            item = {"linha": k}
            if "cpf" in mapa and not _vazio(celula(linha, "cpf")):
                item["cpf"] = converter_cpf(celula(linha, "cpf"))
            if "matricula" in mapa and not _vazio(celula(linha, "matricula")):
                item["matricula"] = converter_matricula(celula(linha, "matricula"))
            if "cpf" not in item and "matricula" not in item:
                raise ValueError("linha sem CPF nem matrícula")
            if _vazio(celula(linha, "data")):
                raise ValueError("linha sem data")
            item["data"] = converter_data(celula(linha, "data"))
            if "trabalhado" in mapa:
                item["trabalhado"] = converter_minutos(celula(linha, "trabalhado"))
            if "extras" in mapa:
                item["extras"] = converter_minutos(celula(linha, "extras"))
            if "falta" in mapa:
                item["falta"] = converter_falta(celula(linha, "falta"))
            if "saldo" in mapa:
                item["saldo"] = converter_minutos(celula(linha, "saldo"), permite_negativo=True)
            if "observacao" in mapa:
                item["observacao"] = str(celula(linha, "observacao") or "").strip()[:LIMITE_OBSERVACAO]
            if "batidas" in mapa:
                item["batidas"] = _batidas(linha, mapa["batidas"])
            if item.get("falta") and item.get("trabalhado", 0) > 0:
                raise ValueError("marcado como falta, mas com horas trabalhadas")
            resultado.append(item)
        except ValueError as e:
            erros.append({"linha": k, "detalhe": str(e)})
    return {"colunas": set(mapa), "linhas": resultado, "erros": erros}


def achar_colaborador(item, mapa_cpf, mapa_matricula):
    """Retorna (colaborador, None) ou (None, motivo). Só vale quem já está cadastrado."""
    colab = None
    if "cpf" in item:
        colab = mapa_cpf.get(item["cpf"])
        if colab is None and "matricula" not in item:
            return None, "CPF não cadastrado"
    if colab is None and "matricula" in item:
        colab = mapa_matricula.get(item["matricula"])
        if colab is None:
            return None, "CPF e matrícula não cadastrados" if "cpf" in item else "matrícula não cadastrada"
    return colab, None
