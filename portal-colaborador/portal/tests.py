import io
import re
import tempfile
import zipfile
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Colaborador, Holerite, LogAcesso, RegistroPonto

SENHA = "senha-forte-123"


class PortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.ana = Colaborador.objects.create(
            usuario=User.objects.create_user("ana", password=SENHA),
            matricula="1", cpf="11111111111", deve_trocar_senha=False)
        cls.bruno = Colaborador.objects.create(
            usuario=User.objects.create_user("bruno", password=SENHA),
            matricula="2", cpf="22222222222", deve_trocar_senha=False)

    def setUp(self):
        cache.clear()

    def test_exige_login(self):
        r = self.client.get(reverse("holerites"))
        self.assertRedirects(r, f"{reverse('login')}?next={reverse('holerites')}")

    def test_holerite_de_outro_colaborador_da_404(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            h = Holerite(colaborador=self.bruno, competencia=date(2026, 9, 1), tipo="mensal")
            h.arquivo.save("x.pdf", ContentFile(b"%PDF-1.4"), save=True)
            self.client.login(username="ana", password=SENHA)
            self.assertEqual(self.client.get(reverse("holerite_arquivo", args=[h.pk])).status_code, 404)
            self.client.login(username="bruno", password=SENHA)
            r = self.client.get(reverse("holerite_arquivo", args=[h.pk]))
            self.assertEqual(r.status_code, 200)
            r.close()

    def test_troca_de_senha_obrigatoria_no_primeiro_acesso(self):
        self.ana.deve_trocar_senha = True
        self.ana.save()
        self.client.login(username="ana", password=SENHA)
        self.assertRedirects(self.client.get(reverse("holerites")), reverse("password_change"))

    def test_bloqueio_apos_varias_falhas(self):
        for _ in range(settings.LOGIN_MAX_TENTATIVAS):
            self.client.post(reverse("login"), {"username": "ana", "password": "errada"})
        r = self.client.post(reverse("login"), {"username": "ana", "password": SENHA})
        self.assertContains(r, "Muitas tentativas")
        self.assertNotIn("_auth_user_id", self.client.session)

    def _holerite(self, colab):
        h = Holerite(colaborador=colab, competencia=date(2026, 9, 1), tipo="mensal")
        h.arquivo.save("x.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return h

    def test_tentativa_em_documento_de_outro_fica_no_log(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            h = self._holerite(self.bruno)
            self.client.login(username="ana", password=SENHA)
            self.client.get(reverse("holerite_arquivo", args=[h.pk]))
            self.assertTrue(LogAcesso.objects.filter(acao="acesso_negado", usuario=self.ana.usuario).exists())

    def test_arquivo_tem_nome_aleatorio_no_disco(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            h = self._holerite(self.ana)
            self.assertRegex(h.arquivo.name, r"^holerites/[0-9a-f]{32}\.pdf$")

    def test_ponto_mostra_so_dados_do_proprio_colaborador(self):
        RegistroPonto.objects.create(colaborador=self.bruno, data=date(2026, 9, 3), observacao="segredo-do-bruno")
        url = reverse("ponto") + "?mes=2026-09"
        self.client.login(username="ana", password=SENHA)
        self.assertNotContains(self.client.get(url), "segredo-do-bruno")
        self.client.login(username="bruno", password=SENHA)
        self.assertContains(self.client.get(url), "segredo-do-bruno")

    def test_rh_precisa_de_permissao_e_acesso_fica_no_log(self):
        User = get_user_model()
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            h = self._holerite(self.bruno)
            url = reverse("holerite_arquivo_rh", args=[h.pk])
            User.objects.create_user("staff", password=SENHA, is_staff=True)
            self.client.login(username="staff", password=SENHA)
            self.assertEqual(self.client.get(url).status_code, 403)
            User.objects.create_superuser("rh", password=SENHA)
            self.client.login(username="rh", password=SENHA)
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            r.close()
            self.assertTrue(LogAcesso.objects.filter(acao="holerite_aberto_pelo_rh").exists())

    def test_filtro_de_holerites_por_ano(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            for ano in (2025, 2026):
                h = Holerite(colaborador=self.ana, competencia=date(ano, 3, 1), tipo="mensal")
                h.arquivo.save("x.pdf", ContentFile(b"%PDF-1.4"), save=True)
            self.client.login(username="ana", password=SENHA)
            r = self.client.get(reverse("holerites") + "?ano=2025")
            self.assertContains(r, "março de 2025")
            self.assertNotContains(r, "março de 2026")

    def _importar(self, arquivos, mes="2026-09", tipo="mensal"):
        get_user_model().objects.create_superuser("rh", password=SENHA)
        self.client.login(username="rh", password=SENHA)
        return self.client.post(reverse("holerites_importar"), {"mes": mes, "tipo": tipo, "arquivos": arquivos})

    def test_importacao_identifica_colaborador_pelo_cpf_no_nome(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            r = self._importar([SimpleUploadedFile("holerite_111.111.111-11.pdf", b"%PDF-1.4 teste")])
            self.assertContains(r, "Importado")
            h = Holerite.objects.get(colaborador=self.ana)
            self.assertEqual((h.competencia, h.tipo), (date(2026, 9, 1), "mensal"))

    def test_importacao_recusa_cpf_desconhecido_e_arquivo_invalido(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            r = self._importar([SimpleUploadedFile("99999999999.pdf", b"%PDF-1.4 x"),
                                SimpleUploadedFile("11111111111.pdf", b"isto nao e pdf")])
            self.assertContains(r, "Não importado")
            self.assertEqual(Holerite.objects.count(), 0)

    def test_importacao_substitui_holerite_do_mesmo_mes(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            self._importar([SimpleUploadedFile("11111111111.pdf", b"%PDF-1.4 a")])
            r = self.client.post(reverse("holerites_importar"), {
                "mes": "2026-09", "tipo": "mensal",
                "arquivos": [SimpleUploadedFile("11111111111.pdf", b"%PDF-1.4 b")]})
            self.assertContains(r, "Substituído")
            self.assertEqual(Holerite.objects.filter(colaborador=self.ana).count(), 1)

    def test_importacao_aceita_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("11111111111.pdf", b"%PDF-1.4 a")
            z.writestr("pasta/22222222222.pdf", b"%PDF-1.4 b")
        with tempfile.TemporaryDirectory() as pasta, override_settings(PRIVATE_MEDIA_ROOT=pasta):
            self._importar([SimpleUploadedFile("setembro.zip", buf.getvalue())])
            self.assertEqual(Holerite.objects.count(), 2)

    def test_importacao_exige_permissao(self):
        self.client.login(username="ana", password=SENHA)
        self.assertEqual(self.client.get(reverse("holerites_importar")).status_code, 403)
