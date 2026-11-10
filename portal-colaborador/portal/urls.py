from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_espelho, views_ponto

urlpatterns = [
    path("", views.home, name="home"),
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("senha/alterar/", views.TrocarSenhaView.as_view(), name="password_change"),
    path("senha/recuperar/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("senha/recuperar/enviado/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("senha/redefinir/<uidb64>/<token>/", views.RedefinirSenhaView.as_view(), name="password_reset_confirm"),
    path("senha/redefinir/concluido/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    # holerites
    path("holerites/", views.holerites, name="holerites"),
    path("holerites/<int:pk>/arquivo/", views.holerite_arquivo, name="holerite_arquivo"),
    path("rh/holerites/importar/", views.holerites_importar, name="holerites_importar"),
    path("rh/holerites/<int:pk>/arquivo/", views.holerite_arquivo_rh, name="holerite_arquivo_rh"),
    # ponto: registros do dia (planilha) e espelhos em PDF
    path("ponto/", views.ponto, name="ponto"),
    path("ponto/espelhos/", views_espelho.espelhos_ponto, name="espelhos_ponto"),
    path("ponto/espelhos/<int:pk>/arquivo/", views_espelho.espelho_arquivo, name="espelho_arquivo"),
    path("rh/ponto/importar/", views_ponto.ponto_importar, name="ponto_importar"),
    path("rh/ponto/modelo.csv", views_ponto.ponto_modelo, name="ponto_modelo"),
    path("rh/ponto/espelhos/importar/", views_espelho.espelhos_importar, name="espelhos_importar"),
    path("rh/ponto/espelhos/<int:pk>/arquivo/", views_espelho.espelho_arquivo_rh, name="espelho_arquivo_rh"),
]
