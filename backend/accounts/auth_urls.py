"""Маршруты входа, выхода, регистрации и смены пароля.

Вынесены из пространства имён ``accounts``, чтобы имена ``login`` и ``logout``
совпадали с ожиданиями штатных механизмов Django (декоратор ``login_required``,
настройка ``LOGIN_URL``).
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    # Смена пароля авторизованным пользователем.
    path(
        "password/change/",
        auth_views.PasswordChangeView.as_view(
            template_name="registration/password_change.html",
            success_url="/password/change/done/",
        ),
        name="password_change",
    ),
    path(
        "password/change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="registration/password_change_done.html"
        ),
        name="password_change_done",
    ),
    # Публичная ссылка на сохранённый вид.
    path("v/<str:token>/", views.shared_view, name="shared_view"),
]
