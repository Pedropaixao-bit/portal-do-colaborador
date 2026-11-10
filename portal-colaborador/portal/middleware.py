from django.shortcuts import redirect

from .models import Colaborador

CAMINHOS_LIVRES = ("/senha/", "/sair/", "/static/")


class TrocaSenhaObrigatoriaMiddleware:
    """Enquanto o colaborador não trocar a senha temporária, só vê a tela de troca."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.path.startswith(CAMINHOS_LIVRES):
            if Colaborador.objects.filter(usuario=request.user, deve_trocar_senha=True).exists():
                return redirect("password_change")
        return self.get_response(request)
