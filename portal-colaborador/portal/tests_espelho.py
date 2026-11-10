import io
import tempfile
import zipfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Colaborador, EspelhoPonto, LogAcesso

SENHA = "senha-forte-123"


class EspelhoPontoTests(TestCase):
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

    def _importar(self, arquivos, mes="2026-09"):
        self.client.login(username="rh", password=SENHA)
        return self.client.post(reverse("espelhos_importar"), {"mes": mes, "arquivos": arquivos})

    def _espelho(self, colab, mes=9, ano=2026):
        e = EspelhoPonto(colaborador=colab, competencia=date(ano, mes, 1))
        e.arquivo.save("x.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return e

    def test_importa_pelo_cpf_no_nome(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            r = self._importar([SimpleUploadedFile("ponto_111.111.111-11.pdf", b"%PDF-1.4 teste")])
            self.assertContains(r, "Importado")
            e = EspelhoPonto.objects.get(colaborador=self.ana)
            self.assertEqual(e.competencia, date(2026, 9, 1))
            self.assertRegex(e.arquivo.name, r"^espelhos_ponto/[0-9a-f]{32}\.pdf$")
            self.assertTrue(LogAcesso.objects.filter(acao="ponto_pdf_importado").exists())

    def test_recusa_cpf_desconhecido_e_arquivo_invalido(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            r = self._importar([SimpleUploadedFile("99999999999.pdf", b"%PDF-1.4 x"),
                                SimpleUploadedFile("11111111111.pdf", b"isto nao e pdf")])
            self.assertContains(r, "Não importado")
            self.assertEqual(EspelhoPonto.objects.count(), 0)

    def test_importar_de_novo_substitui_o_do_mesmo_mes(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            self._importar([SimpleUploadedFile("11111111111.pdf", b"%PDF-1.4 a")])
            r = self._importar([SimpleUploadedFile("11111111111.pdf", b"%PDF-1.4 b")])
            self.assertContains(r, "Substituído")
            self.assertEqual(EspelhoPonto.objects.filter(colaborador=self.ana).count(), 1)

    def test_aceita_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("11111111111.pdf", b"%PDF-1.4 a")
            z.writestr("pasta/22222222222.pdf", b"%PDF-1.4 b")
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            self._importar([SimpleUploadedFile("setembro.zip", buf.getvalue())])
            self.assertEqual(EspelhoPonto.objects.count(), 2)

    def test_importacao_exige_permissao(self):
        self.client.login(username="ana", password=SENHA)
        self.assertEqual(self.client.get(reverse("espelhos_importar")).status_code, 403)

    def test_funcionario_ve_so_os_proprios_espelhos(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            self._espelho(self.ana, mes=9)
            self._espelho(self.bruno, mes=8)
            self.client.login(username="ana", password=SENHA)
            r = self.client.get(reverse("espelhos_ponto"))
            self.assertContains(r, "Setembro de 2026")
            self.assertNotContains(r, "Agosto de 2026")

    def test_espelho_de_outro_da_404_e_fica_no_log(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            e = self._espelho(self.bruno)
            self.client.login(username="ana", password=SENHA)
            self.assertEqual(self.client.get(reverse("espelho_arquivo", args=[e.pk])).status_code, 404)
            self.assertTrue(LogAcesso.objects.filter(acao="acesso_negado", usuario=self.ana.usuario).exists())
            self.client.login(username="bruno", password=SENHA)
            r = self.client.get(reverse("espelho_arquivo", args=[e.pk]))
            self.assertEqual(r.status_code, 200)
            r.close()

    def test_rh_precisa_de_permissao_para_abrir(self):
        User = get_user_model()
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            e = self._espelho(self.bruno)
            url = reverse("espelho_arquivo_rh", args=[e.pk])
            User.objects.create_user("staff", password=SENHA, is_staff=True)
            self.client.login(username="staff", password=SENHA)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.login(username="rh", password=SENHA)
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            r.close()
            self.assertTrue(LogAcesso.objects.filter(acao="ponto_aberto_pelo_rh").exists())
