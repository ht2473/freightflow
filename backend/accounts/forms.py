"""Формы модуля учётных записей."""

from __future__ import annotations

from core.models import District
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import IncidentSubscription, SavedView, UserProfile


class RegistrationForm(UserCreationForm):
    """Регистрация пользователя с обязательными контактными сведениями.

    Расширяет штатную форму Django полями имени, фамилии и адреса почты:
    без них администратору сложно сопоставить учётную запись с реальным
    сотрудником при выдаче расширенных прав.
    """

    first_name = forms.CharField(label="Имя", max_length=150)
    last_name = forms.CharField(label="Фамилия", max_length=150)
    email = forms.EmailField(label="Адрес электронной почты")
    organization = forms.CharField(
        label="Организация", max_length=200, required=False,
        help_text="Указывается при запросе расширенных прав доступа",
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def clean_email(self) -> str:
        """Не допустить повторной регистрации одного адреса."""
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Учётная запись с этим адресом уже зарегистрирована."
            )
        return email

    def save(self, commit: bool = True) -> User:
        """Сохранить пользователя и заполнить профиль организацией."""
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        if commit:
            user.save()
            # Профиль создаётся сигналом post_save; здесь только дополняем его.
            organization = self.cleaned_data.get("organization", "")
            if organization:
                UserProfile.objects.filter(user=user).update(organization=organization)
        return user


class ProfileForm(forms.ModelForm):
    """Редактирование профиля и предпочтений интерфейса.

    Роль в форме не участвует: её назначает администратор. Это исключает
    возможность самостоятельного повышения полномочий.
    """

    first_name = forms.CharField(label="Имя", max_length=150, required=False)
    last_name = forms.CharField(label="Фамилия", max_length=150, required=False)
    email = forms.EmailField(label="Адрес электронной почты", required=False)

    class Meta:
        model = UserProfile
        fields = (
            "organization",
            "position",
            "phone",
            "theme",
            "language",
            "default_district",
            "notify_incidents",
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["default_district"].queryset = District.objects.all()
        self.fields["default_district"].empty_label = "Не выбран"
        if user:
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name
            self.fields["email"].initial = user.email

    def save(self, commit: bool = True) -> UserProfile:
        """Сохранить профиль и синхронизировать данные учётной записи."""
        profile = super().save(commit=commit)
        if self.user and commit:
            self.user.first_name = self.cleaned_data.get("first_name", "")
            self.user.last_name = self.cleaned_data.get("last_name", "")
            self.user.email = self.cleaned_data.get("email", "")
            self.user.save(update_fields=["first_name", "last_name", "email"])
        return profile


class SavedViewForm(forms.ModelForm):
    """Сохранение текущего набора условий отбора."""

    class Meta:
        model = SavedView
        fields = ("title", "page", "query", "description")
        widgets = {
            "page": forms.TextInput(attrs={"placeholder": "core:object_list"}),
            "query": forms.TextInput(attrs={"placeholder": "district=1&type=2"}),
            "description": forms.TextInput(
                attrs={"placeholder": "Для чего нужен этот вид (необязательно)"}
            ),
        }

    def clean_page(self) -> str:
        """Проверить, что имя маршрута разрешимо."""
        from django.urls import NoReverseMatch, reverse

        page = (self.cleaned_data.get("page") or "").strip()
        candidate = page if ":" in page else f"core:{page}"
        try:
            reverse(candidate)
        except NoReverseMatch as exc:
            raise forms.ValidationError(
                "Указанная страница не найдена. Сохраняйте вид кнопкой на самой странице."
            ) from exc
        return page


class SubscriptionForm(forms.ModelForm):
    """Оформление подписки на дорожные события."""

    class Meta:
        model = IncidentSubscription
        fields = ("district", "min_severity", "cargo_only")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = District.objects.all()
        self.fields["district"].empty_label = "Вся территория города"
        self.fields["min_severity"] = forms.TypedChoiceField(
            label="Минимальная серьёзность",
            coerce=int,
            choices=[
                (1, "1 — незначительные и выше"),
                (2, "2 — умеренные и выше"),
                (3, "3 — значительные и выше"),
                (4, "4 — серьёзные и выше"),
                (5, "5 — только критические"),
            ],
            initial=3,
        )
