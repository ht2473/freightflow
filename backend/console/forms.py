"""Формы панели администратора."""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from etl import registry
from etl.upload import COLUMNS

#: Расширения, принимаемые формой выгрузки.
ALLOWED_SUFFIXES = (".csv", ".txt", ".tsv", ".xlsx", ".xlsm")


class PipelineRunForm(forms.Form):
    """Запуск загрузки набора данных."""

    pipeline = forms.ChoiceField(label=_("Набор данных"), choices=())
    refresh = forms.BooleanField(
        label=_("Обратиться к источнику заново"), required=False,
        help_text=_("Без отметки используются сохранённые ответы службы"),
    )
    prune = forms.BooleanField(
        label=_("Привести реестр к составу источника"), required=False,
        help_text=_("Записи, отсутствующие в выгрузке, удаляются"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pipeline"].choices = [
            (item.name, f"{item.title} → {item.target_table}")
            for item in registry.available()
            if item.console_enabled and not item.expects_upload
        ]

    def clean(self) -> dict:
        data = super().clean()
        name = data.get("pipeline")
        if name and data.get("prune"):
            pipeline = registry.get(name)
            if not pipeline.supports_prune:
                raise forms.ValidationError(
                    _("Набор «%(title)s» приведению к составу источника "
                      "не подлежит: справочник закрыт и записи из него "
                      "не удаляются.") % {"title": pipeline.title}
                )
        return data


class FlowUploadForm(forms.Form):
    """Выгрузка ряда грузопотоков, присылаемая пользователем."""

    file = forms.FileField(
        label=_("Файл выгрузки"),
        help_text=_("CSV либо книга Excel. Ожидаемые колонки перечислены ниже"),
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (uploaded.name or "").lower()
        if not name.endswith(ALLOWED_SUFFIXES):
            raise forms.ValidationError(
                _("Принимаются файлы %(suffixes)s")
                % {"suffixes": ", ".join(ALLOWED_SUFFIXES)}
            )
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        if uploaded.size > limit:
            raise forms.ValidationError(
                _("Файл больше %(limit)d МБ") % {"limit": limit // (1024 * 1024)}
            )
        return uploaded

    @property
    def expected_columns(self):
        """Состав ожидаемых колонок — для пояснения рядом с формой."""
        return COLUMNS
