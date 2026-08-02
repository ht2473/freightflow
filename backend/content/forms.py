"""Формы модуля информационного наполнения."""

from __future__ import annotations

from django import forms

from .models import FeedbackMessage

# Допустимые расширения вложений к обращению. Список ограничен форматами,
# в которых обычно присылают подтверждающие материалы.
ALLOWED_ATTACHMENTS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".csv", ".docx"}
MAX_ATTACHMENT_MB = 10


class FeedbackForm(forms.ModelForm):
    """Форма обратной связи с проверкой вложения и защитой от роботов."""

    # Скрытое поле-ловушка: настоящий пользователь его не заполняет, тогда как
    # автоматические рассыльщики заполняют все поля формы подряд.
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    consent = forms.BooleanField(
        label="Согласен на обработку указанных данных для ответа на обращение",
        required=True,
    )

    class Meta:
        model = FeedbackMessage
        fields = ("name", "email", "organization", "topic", "message", "attachment")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Иванов Иван Иванович"}),
            "email": forms.EmailInput(attrs={"placeholder": "name@example.ru"}),
            "organization": forms.TextInput(attrs={"placeholder": "Организация (необязательно)"}),
            "message": forms.Textarea(
                attrs={"rows": 7, "placeholder": "Опишите вопрос или замечание"}
            ),
        }

    def clean_website(self) -> str:
        """Отклонить отправку, если заполнено поле-ловушка."""
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Обращение отклонено системой защиты.")
        return ""

    def clean_message(self) -> str:
        """Проверить содержательность обращения."""
        message = (self.cleaned_data.get("message") or "").strip()
        if len(message) < 20:
            raise forms.ValidationError(
                "Опишите обращение подробнее — не менее 20 символов."
            )
        return message

    def clean_attachment(self):
        """Проверить формат и размер вложения."""
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        name = attachment.name.lower()
        if not any(name.endswith(ext) for ext in ALLOWED_ATTACHMENTS):
            raise forms.ValidationError(
                "Допустимые форматы вложения: " + ", ".join(sorted(ALLOWED_ATTACHMENTS))
            )
        if attachment.size > MAX_ATTACHMENT_MB * 1024 * 1024:
            raise forms.ValidationError(f"Размер вложения не должен превышать {MAX_ATTACHMENT_MB} МБ.")
        return attachment
