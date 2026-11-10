import io
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Colaborador, LogAcesso, RegistroPonto

SENHA = "senha-forte-123"

CSV = (
    "CPF;Data;Horas trabalhadas;Horas extras;Falta;Saldo banco de horas\n"
    "111.111.111-11;01/09/2026;08:00;00:00;Não;00:00\n"
    "111.111.111-11;02/09/2026;09:30;01:30;Não;01:30\n"
    "22222222222;01/09/2026;00:00;00:00;Sim;-08:00\n"
)


class ImportacaoPontoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.ana = Colaborador.objects.create(
            usuario=User.objects.create_user("ana", password=SENHA),
            matricula="1", cpf="11111111111", deve_trocar_senha=False)
        cls.bruno = Colaborador.objects.create(
            usuario=User.objects.create_user("bruno", password=SENHA),
            matricula="2", cpf="22222222222", deve_trocar_senha=False)
        User.objects.create_superuser("rh", password=SENHA)

    def _previa(self, conteudo, nome="ponto.csv"):
        self.client.login(username="rh", password=SENHA)
        if isinstance(conteudo, str):
            conteudo = conteudo.encode("utf-8")
        return self.client.post(reverse("ponto_importar"), {"arquivos": SimpleUploadedFile(nome, conteudo)})

    def _importar(self, conteudo, nome="ponto.csv"):
        """Envia o relatório e confirma a prévia. Se não há nada para gravar, devolve a própria prévia."""
        previa = self._previa(conteudo, nome)
        token = previa.context.get("token") if previa.context else None
        if not token:
            return previa
        return self.client.post(reverse("ponto_importar"), {"confirmar": "1", "token": token})

    def test_previa_nao_grava_nada(self):
        r = self._previa(CSV)
        self.assertContains(r, "Prévia")
        self.assertContains(r, "3 dia(s) serão gravados")
        self.assertEqual(RegistroPonto.objects.count(), 0)

    def test_importa_registros_pelo_cpf(self):
        r = self._importar(CSV)
        self.assertContains(r, "3 dia(s) importado(s)")
        dia = RegistroPonto.objects.get(colaborador=self.ana, data=date(2026, 9, 2))
        self.assertEqual((dia.minutos_trabalhados, dia.minutos_extras), (570, 90))
        self.assertTrue(RegistroPonto.objects.get(colaborador=self.bruno, data=date(2026, 9, 1)).falta)
        self.assertTrue(LogAcesso.objects.filter(acao="ponto_importado").exists())

    def test_confirmar_com_token_invalido_nao_grava(self):
        self._previa(CSV)
        r = self.client.post(reverse("ponto_importar"), {"confirmar": "1", "token": "errado"})
        self.assertContains(r, "A prévia expirou")
        self.assertEqual(RegistroPonto.objects.count(), 0)

    def test_importar_de_novo_atualiza_sem_duplicar(self):
        self._importar(CSV)
        r = self._importar(CSV.replace("09:30", "10:00"))
        self.assertContains(r, "Dias atualizados")
        self.assertEqual(RegistroPonto.objects.filter(colaborador=self.ana).count(), 2)
        self.assertEqual(RegistroPonto.objects.get(colaborador=self.ana, data=date(2026, 9, 2)).minutos_trabalhados, 600)

    def test_recusa_cpf_desconhecido_e_linha_invalida(self):
        r = self._importar(
            "CPF;Data;Horas trabalhadas\n"
            "99999999999;01/09/2026;08:00\n"
            "11111111111;32/13/2026;08:00\n"
            "11111111111;03/09/2026;oito horas\n"
            "11111111111;04/09/2026;08:00\n")
        self.assertContains(r, "CPF não cadastrado")
        self.assertContains(r, "1 dia(s) importado(s), 3 com erro")
        self.assertEqual(RegistroPonto.objects.count(), 1)

    def test_identifica_por_matricula_quando_nao_tem_cpf(self):
        self._importar("Matrícula;Data;Horas trabalhadas\n2;01/09/2026;07:00\n")
        self.assertEqual(RegistroPonto.objects.get(colaborador=self.bruno).minutos_trabalhados, 420)

    def test_matricula_com_zeros_a_esquerda(self):
        c = Colaborador.objects.create(
            usuario=get_user_model().objects.create_user("carla", password=SENHA),
            matricula="0045", cpf="33333333333", deve_trocar_senha=False)
        self._importar("Matrícula;Data;Horas trabalhadas\n45;01/09/2026;07:00\n")
        self.assertEqual(RegistroPonto.objects.get(colaborador=c).minutos_trabalhados, 420)

    def test_importa_batidas_de_entrada_e_saida(self):
        self._importar(
            "CPF;Data;Entrada 1;Saída 1;Entrada 2;Saída 2;Horas trabalhadas\n"
            "11111111111;01/09/2026;08:00;12:00;13:00;17:00;08:00\n")
        self.assertEqual(RegistroPonto.objects.get(colaborador=self.ana).batidas, "08:00 12:00 13:00 17:00")

    def test_falta_com_horas_trabalhadas_e_recusada(self):
        r = self._importar("CPF;Data;Horas trabalhadas;Falta\n11111111111;01/09/2026;08:00;Sim\n")
        self.assertContains(r, "marcado como falta")
        self.assertEqual(RegistroPonto.objects.count(), 0)

    def test_observacao_longa_e_cortada_no_limite_do_banco(self):
        self._importar("CPF;Data;Observação\n11111111111;01/09/2026;" + "x" * 600 + "\n")
        self.assertEqual(len(RegistroPonto.objects.get(colaborador=self.ana).observacao), 200)

    def test_aceita_xlsx(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Relatório de ponto - setembro"])
        ws.append([])
        ws.append(["CPF", "Data", "Horas trabalhadas"])
        ws.append(["111.111.111-11", date(2026, 9, 1), "08:00"])
        buf = io.BytesIO()
        wb.save(buf)
        self._importar(buf.getvalue(), nome="ponto.xlsx")
        self.assertEqual(RegistroPonto.objects.get(colaborador=self.ana).minutos_trabalhados, 480)

    def test_arquivo_sem_cabecalho_e_recusado(self):
        r = self._importar("nome;idade\nAna;30\n")
        self.assertContains(r, "não encontrei o cabeçalho")
        self.assertEqual(RegistroPonto.objects.count(), 0)

    def test_funcionario_ve_o_ponto_importado_como_presente(self):
        self._importar(CSV)
        self.client.login(username="ana", password=SENHA)
        r = self.client.get(reverse("ponto") + "?mes=2026-09")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["registros"].count(), 2)
        self.assertContains(r, "Presente")

    def test_exige_permissao_de_rh(self):
        self.client.login(username="ana", password=SENHA)
        self.assertEqual(self.client.get(reverse("ponto_importar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("ponto_modelo")).status_code, 403)

    def test_modelo_csv_baixa(self):
        self.client.login(username="rh", password=SENHA)
        r = self.client.get(reverse("ponto_modelo"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Horas trabalhadas")
