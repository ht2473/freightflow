"""Информационное наполнение системы и обратная связь.

Модуль обеспечивает содержательную часть портала: аналитические обзоры по
логистике города, методические материалы и обращения пользователей. Материалы
редактируются через панель администратора, публикуются по расписанию и
доступны неавторизованным посетителям.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class ArticleCategory(models.Model):
    """Рубрика аналитических материалов."""

    code = models.SlugField(_("Код"), max_length=48, unique=True)
    name = models.CharField(_("Наименование"), max_length=120)
    description = models.CharField(_("Описание"), max_length=400, blank=True, default="")
    order = models.SmallIntegerField(_("Порядок вывода"), default=100)

    class Meta:
        db_table = "app_article_category"
        ordering = ("order", "name")
        verbose_name = _("Рубрика материалов")
        verbose_name_plural = _("Рубрики материалов")

    def __str__(self) -> str:
        return self.name

    @property
    def published_count(self) -> int:
        """Число опубликованных материалов в рубрике."""
        return self.articles.filter(is_published=True).count()


class ArticleQuerySet(models.QuerySet):
    """Типовые выборки аналитических материалов."""

    def published(self) -> ArticleQuerySet:
        """Материалы, доступные посетителям сайта."""
        return self.filter(is_published=True, published_at__lte=timezone.now())

    def featured(self) -> ArticleQuerySet:
        """Материалы, вынесенные на главную страницу."""
        return self.published().filter(is_featured=True)


class Article(models.Model):
    """Аналитический материал портала."""

    category = models.ForeignKey(
        ArticleCategory,
        on_delete=models.PROTECT,
        related_name="articles",
        verbose_name=_("Рубрика"),
    )
    slug = models.SlugField(_("Адресный код"), max_length=180, unique=True)
    title = models.CharField(_("Заголовок"), max_length=250)
    lead = models.CharField(_("Краткое содержание"), max_length=600, blank=True, default="")
    body = models.TextField(_("Текст материала"))
    reading_minutes = models.SmallIntegerField(_("Время чтения, мин"), default=5)

    is_published = models.BooleanField(_("Опубликован"), default=True)
    is_featured = models.BooleanField(_("На главной"), default=False)
    published_at = models.DateTimeField(_("Дата публикации"), default=timezone.now)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        verbose_name=_("Автор"),
    )
    view_count = models.IntegerField(_("Число просмотров"), default=0)

    created_at = models.DateTimeField(_("Создан"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Обновлён"), auto_now=True)

    objects = ArticleQuerySet.as_manager()

    class Meta:
        db_table = "app_article"
        ordering = ("-published_at",)
        verbose_name = _("Аналитический материал")
        verbose_name_plural = _("Аналитические материалы")
        indexes = [models.Index(fields=["-published_at"], name="idx_article_published")]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        """Сформировать адресный код из заголовка, если он не задан вручную."""
        if not self.slug:
            self.slug = slugify(self.title, allow_unicode=False)[:180] or f"material-{self.pk}"
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("content:article_detail", args=[self.slug])

    def register_view(self) -> None:
        """Увеличить счётчик просмотров без выборки объекта целиком."""
        Article.objects.filter(pk=self.pk).update(view_count=models.F("view_count") + 1)

    @property
    def paragraphs(self) -> list[str]:
        """Разбить текст материала на абзацы для вывода в шаблоне."""
        return [p.strip() for p in self.body.split("\n\n") if p.strip()]


class FeedbackMessage(models.Model):
    """Обращение, поступившее через форму обратной связи."""

    class Topic(models.TextChoices):
        DATA = "data", _("Замечание к данным")
        FEATURE = "feature", _("Предложение по развитию")
        ERROR = "error", _("Сообщение об ошибке")
        ACCESS = "access", _("Вопрос по доступу")
        OTHER = "other", _("Иное")

    class Status(models.TextChoices):
        NEW = "new", _("Новое")
        IN_WORK = "in_work", _("В работе")
        ANSWERED = "answered", _("Отвечено")
        CLOSED = "closed", _("Закрыто")

    name = models.CharField(_("Представление"), max_length=150)
    email = models.EmailField(_("Адрес электронной почты"))
    organization = models.CharField(_("Организация"), max_length=200, blank=True, default="")
    topic = models.CharField(_("Тема обращения"), max_length=16, choices=Topic.choices)
    message = models.TextField(_("Текст обращения"))
    attachment = models.FileField(
        _("Вложение"), upload_to="feedback/%Y/%m/", null=True, blank=True
    )

    status = models.CharField(
        _("Состояние"), max_length=16, choices=Status.choices, default=Status.NEW, db_index=True
    )
    answer = models.TextField(_("Ответ"), blank=True, default="")
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answered_feedback",
        verbose_name=_("Обработал"),
    )
    answered_at = models.DateTimeField(_("Дата ответа"), null=True, blank=True)

    # Служебные сведения помогают отличить массовые автоматические отправки.
    ip_address = models.GenericIPAddressField(_("IP-адрес"), null=True, blank=True)
    user_agent = models.CharField(_("Клиент"), max_length=300, blank=True, default="")
    created_at = models.DateTimeField(_("Поступило"), auto_now_add=True, db_index=True)

    class Meta:
        db_table = "app_feedback_message"
        ordering = ("-created_at",)
        verbose_name = _("Обращение")
        verbose_name_plural = _("Обратная связь")

    def __str__(self) -> str:
        return f"{self.get_topic_display()} · {self.name}"

    @property
    def is_open(self) -> bool:
        """Обращение ещё не обработано."""
        return self.status in {self.Status.NEW, self.Status.IN_WORK}

    @property
    def age_days(self) -> int:
        """Возраст обращения в сутках — для контроля сроков обработки."""
        return (timezone.now() - self.created_at).days

    def mark_answered(self, user, answer: str) -> None:
        """Зафиксировать ответ на обращение."""
        self.answer = answer
        self.answered_by = user
        self.answered_at = timezone.now()
        self.status = self.Status.ANSWERED
        self.save(update_fields=["answer", "answered_by", "answered_at", "status"])


class StaticPage(models.Model):
    """Редактируемая информационная страница портала.

    Модель обслуживает разделы, содержание которых меняется без участия
    разработчика: «О системе», «Методология», пользовательское соглашение.
    """

    code = models.SlugField(_("Код страницы"), max_length=48, unique=True)
    title = models.CharField(_("Заголовок"), max_length=250)
    lead = models.CharField(_("Вводный текст"), max_length=600, blank=True, default="")
    body = models.TextField(_("Содержание"))
    is_published = models.BooleanField(_("Опубликована"), default=True)
    updated_at = models.DateTimeField(_("Обновлена"), auto_now=True)

    class Meta:
        db_table = "app_static_page"
        ordering = ("code",)
        verbose_name = _("Информационная страница")
        verbose_name_plural = _("Информационные страницы")

    def __str__(self) -> str:
        return self.title

    @property
    def paragraphs(self) -> list[str]:
        """Разбить содержание на абзацы."""
        return [p.strip() for p in self.body.split("\n\n") if p.strip()]
