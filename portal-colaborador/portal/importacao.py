"""Leitura dos PDFs de holerite enviados em lote (sem depender do Django)."""
import io
import os
import re
import zipfile

LIMITE_PDF = 5 * 1024 * 1024      # 5 MB por PDF
LIMITE_ARQUIVOS = 1000            # PDFs por importação

# 11 dígitos de CPF, com ou sem pontos e traço, sem fazer parte de um número maior.
_CPF = re.compile(r"(?<!\d)(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})(?!\d)")


def cpfs_no_texto(texto):
    return {"".join(m.groups()) for m in _CPF.finditer(texto or "")}


def texto_do_pdf(dados, paginas=3):
    """Texto das primeiras páginas. None se a biblioteca não existe; '' se não há texto."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        leitor = PdfReader(io.BytesIO(dados))
        return "\n".join((p.extract_text() or "") for p in leitor.pages[:paginas])
    except Exception:
        return ""


def identificar(nome_arquivo, dados, mapa_cpf):
    """Descobre o colaborador pelo CPF no nome do arquivo ou, se faltar, dentro do PDF.

    mapa_cpf: {cpf_só_números: colaborador}. Só vale CPF que esteja cadastrado.
    Retorna (colaborador, None) ou (None, motivo).
    """
    achados = [c for c in cpfs_no_texto(nome_arquivo) if c in mapa_cpf]
    if len(achados) == 1:
        return mapa_cpf[achados[0]], None
    if len(achados) > 1:
        return None, "há mais de um CPF cadastrado no nome do arquivo"

    texto = texto_do_pdf(dados)
    if texto is None:
        return None, "CPF não está no nome do arquivo e a leitura do PDF não está instalada (pypdf)"
    if not texto.strip():
        return None, "CPF não está no nome do arquivo e o PDF não tem texto legível (pode ser imagem)"
    achados = [c for c in cpfs_no_texto(texto) if c in mapa_cpf]
    if len(achados) == 1:
        return mapa_cpf[achados[0]], None
    if len(achados) > 1:
        return None, "há mais de um CPF cadastrado dentro do PDF"
    return None, "nenhum CPF cadastrado foi encontrado no nome do arquivo nem dentro do PDF"


def _pdf(nome, tamanho, ler):
    if tamanho > LIMITE_PDF:
        return nome, None, "maior que 5 MB"
    dados = ler()
    if not dados.startswith(b"%PDF-"):
        return nome, None, "não é um PDF válido"
    return nome, dados, None


def pdfs_do_upload(arquivos):
    """Gera (nome, dados, erro) para cada PDF enviado; abre os ZIPs."""
    total = 0
    for arq in arquivos:
        nome = os.path.basename(arq.name)
        baixo = nome.lower()
        if baixo.endswith(".pdf"):
            total += 1
            yield _pdf(nome, arq.size, arq.read)
        elif baixo.endswith(".zip"):
            try:
                z = zipfile.ZipFile(arq)
            except zipfile.BadZipFile:
                yield nome, None, "ZIP inválido ou corrompido"
                continue
            with z:
                for info in z.infolist():
                    base = os.path.basename(info.filename)
                    if info.is_dir() or "__MACOSX" in info.filename or not base.lower().endswith(".pdf"):
                        continue
                    total += 1
                    if total > LIMITE_ARQUIVOS:
                        yield base, None, f"limite de {LIMITE_ARQUIVOS} arquivos por importação"
                        continue
                    yield _pdf(base, info.file_size, lambda i=info: z.read(i))
        else:
            yield nome, None, "formato não aceito (envie PDF ou ZIP)"
