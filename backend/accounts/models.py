"""Операционные сущности: пользователи, роли и рабочее состояние кабинета.

Раздел отделён от доменного слоя (``core``) сознательно. Таблицы ``core``
описывают предметную область и наполняются процедурами загрузки данных, тогда
как таблицы этого модуля хранят изменяемое состояние приложения: профили,
избранное, сохранённые условия отбора, задания на выгрузку и журнал действий.

Важное свойство сохранённых видов: они хранят **условия отбора**, а не сами
данные. При открытии сохранённого вида выборка выполняется заново, поэтому
пользователь всегда получает актуальные показатели.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: Опознавательное начало токена REST API. Значение, найденное в чужом
#: журнале или в истории команд, по нему сразу опознаётся как ключ доступа
#: к этой системе и может быть отозвано владельцем.
API_TOKEN_PREFIX = "ff_"

#: Наименьший промежуток между отметками об использовании токена.
API_TOKEN_USE_INTERVAL = timedelta(minutes=1)


def hash_api_token(raw: str) -> str:
    """Вычислить отпечаток токена для поиска в базе.

    Токен — случайное значение достаточной длины, поэтому перебор словарём
    к нему неприменим и в замедляющей функции с солью нужды нет; от неё,
    напротив, пришлось бы отказаться, так как поиск ведётся по равенству.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Role(models.TextChoices):
    """Роли пользователей системы, упорядоченные по объёму полномочий.

    Модель доступа — вложенная: каждая следующая роль включает права
    предыдущей. Сопоставление ролей и разрешений Django выполняет команда
    ``manage.py setup_roles``.
    """

    VIEWER = "viewer", _("Наблюдатель")
    ANALYST = "analyst", _("Аналитик")
    OPERATOR = "operator", _("Диспетчер")
    ADMIN = "admin", _("Администратор")


#: Порядок ролей для сравнения «не ниже, чем». Индекс — уровень полномочий.
ROLE_ORDER: tuple[str, ...] = (Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN)

#: Краткое описание полномочий роли — используется в справке и в панели
#: администратора при назначении прав.
ROLE_DESCRIPTIONS: dict[str, str] = {
    Role.VIEWER: (
        _("Просмотр реестров, карты и аналитики, ведение избранного и "
        "сохранённых условий отбора.")
    ),
    Role.ANALYST: (
        _("Дополнительно: выгрузка отчётов в форматах XLSX, DOCX, CSV и GeoJSON, "
        "работа с конструктором сравнений и доступ к REST API по токену.")
    ),
    Role.OPERATOR: (
        _("Дополнительно: регистрация и закрытие дорожных инцидентов, "
        "редактирование карточек объектов инфраструктуры, запуск загрузки данных.")
    ),
    Role.ADMIN: (
        _("Полный доступ: управление пользователями и ролями, ведение "
        "справочников, модерация обращений, журнал аудита, настройки системы.")
    ),
}


class UserProfile(models.Model):
    """Расширение учётной записи: роль, предпочтения интерфейса, токен API."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("Пользователь"),
    )
    role = models.CharField(
        _("Роль"), max_length=16, choices=Role.choices, default=Role.VIEWER, db_index=True
    )
    organization = models.CharField(_("Организация"), max_length=200, blank=True, default="")
    position = models.CharField(_("Должность"), max_length=200, blank=True, default="")
    phone = models.CharField(_("Телефон"), max_length=32, blank=True, default="")

    # Предпочтения интерфейса запоминаются между сессиями и применяются
    # при первом открытии соответствующих страниц.
    theme = models.CharField(
        _("Оформление"),
        max_length=8,
        choices=[("auto", _("Как в системе")), ("dark", _("Тёмное")), ("light", _("Светлое"))],
        default="auto",
    )
    language = models.CharField(
        _("Язык интерфейса"), max_length=8, choices=settings.LANGUAGES, default="ru"
    )
    default_district = models.ForeignKey(
        "core.District",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Рабочая область"),
        help_text=_(
            "Округ, которым разделы системы ограничиваются при первом "
            "открытии. Заданный в самом разделе отбор рабочую область "
            "замещает"
        ),
    )
    notify_incidents = models.BooleanField(_("Уведомлять об инцидентах"), default=True)

    # Токен хранится отпечатком: содержимое базы не должно позволять
    # обращаться к системе от имени её пользователей. Начало значения
    # хранится отдельно — по нему владелец узнаёт свой токен в списке,
    # не раскрывая его целиком.
    api_token_hash = models.CharField(
        _("Отпечаток токена"), max_length=64, blank=True, default="", db_index=True
    )
    api_token_prefix = models.CharField(
        _("Начало токена"), max_length=16, blank=True, default=""
    )
    api_token_created = models.DateTimeField(_("Токен выпущен"), null=True, blank=True)
    api_token_used = models.DateTimeField(_("Последнее обращение"), null=True, blank=True)

    created_at = models.DateTimeField(_("Создан"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Обновлён"), auto_now=True)

    class Meta:
        db_table = "app_user_profile"
        verbose_name = _("Профиль пользователя")
        verbose_name_plural = _("Профили пользователей")

    def __str__(self) -> str:
        return f"{self.user.username} · {self.get_role_display()}"

    # --------------------------------------------------------------- права

    @property
    def role_level(self) -> int:
        """Числовой уровень роли для сравнения полномочий."""
        return ROLE_ORDER.index(self.role) if self.role in ROLE_ORDER else 0

    def has_role(self, minimum: str) -> bool:
        """Проверить, что роль пользователя не ниже требуемой."""
        try:
            return self.role_level >= ROLE_ORDER.index(minimum)
        except ValueError:  # pragma: no cover — защита от опечатки в коде
            return False

    @property
    def can_export(self) -> bool:
        """Право выгружать отчёты и пользоваться REST API по токену."""
        return self.has_role(Role.ANALYST)

    @property
    def can_operate(self) -> bool:
        """Право изменять оперативные данные: инциденты, карточки объектов."""
        return self.has_role(Role.OPERATOR)

    @property
    def can_administer(self) -> bool:
        """Право доступа к панели администратора."""
        return self.has_role(Role.ADMIN)

    @property
    def role_description(self) -> str:
        """Описание полномочий текущей роли."""
        return ROLE_DESCRIPTIONS.get(self.role, "")

    # --------------------------------------------------------------- токен

    @property
    def has_api_token(self) -> bool:
        """Действующий токен выпущен."""
        return bool(self.api_token_hash)

    def issue_api_token(self) -> str:
        """Выпустить новый токен доступа к REST API, отозвав предыдущий.

        Значение возвращается вызывающему один раз и больше нигде не
        восстанавливается: в базе остаётся только отпечаток.
        """
        raw = f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        self.api_token_hash = hash_api_token(raw)
        self.api_token_prefix = raw[:12]
        self.api_token_created = timezone.now()
        self.api_token_used = None
        self.save(
            update_fields=[
                "api_token_hash",
                "api_token_prefix",
                "api_token_created",
                "api_token_used",
                "updated_at",
            ]
        )
        return raw

    def revoke_api_token(self) -> None:
        """Отозвать действующий токен."""
        self.api_token_hash = ""
        self.api_token_prefix = ""
        self.api_token_created = None
        self.api_token_used = None
        self.save(
            update_fields=[
                "api_token_hash",
                "api_token_prefix",
                "api_token_created",
                "api_token_used",
                "updated_at",
            ]
        )

    def note_api_use(self) -> None:
        """Отметить обращение по токену.

        Отметка обновляется не чаще раза в минуту: назначение поля —
        показать владельцу, что токеном пользуются, а не считать запросы,
        и запись на каждое обращение к API этому назначению не отвечает.
        """
        now = timezone.now()
        if self.api_token_used and now - self.api_token_used < API_TOKEN_USE_INTERVAL:
            return
        # Точечное обновление: профиль в этот момент собран из запроса,
        # и сохранять его целиком означало бы затирать правки из кабинета.
        UserProfile.objects.filter(pk=self.pk).update(api_token_used=now)
        self.api_token_used = now


class Favorite(models.Model):
    """Закладка пользователя на объект системы.

    Универсальная связь реализована парой «тип сущности + идентификатор»:
    это проще и быстрее обобщённых внешних ключей Django, а список типов
    заведомо ограничен предметной областью.
    """

    class Kind(models.TextChoices):
        OBJECT = "object", _("Объект инфраструктуры")
        DISTRICT = "district", _("Округ")
        ROAD = "road", _("Участок дороги")
        ROUTE = "route", _("Грузовой маршрут")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
        verbose_name=_("Пользователь"),
    )
    kind = models.CharField(_("Тип"), max_length=16, choices=Kind.choices)
    target_id = models.IntegerField(_("Идентификатор объекта"))
    title = models.CharField(_("Наименование"), max_length=250, blank=True, default="")
    note = models.CharField(_("Заметка"), max_length=500, blank=True, default="")
    created_at = models.DateTimeField(_("Добавлено"), auto_now_add=True)

    class Meta:
        db_table = "app_favorite"
        ordering = ("-created_at",)
        verbose_name = _("Закладка")
        verbose_name_plural = _("Избранное")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "target_id"], name="uniq_favorite_per_user"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.title or self.target_id}"

    @property
    def url(self) -> str:
        """Адрес карточки объекта, на который указывает закладка."""
        route = {
            self.Kind.OBJECT: "core:object_detail",
            self.Kind.DISTRICT: "core:district_detail",
            self.Kind.ROAD: "core:road_detail",
            self.Kind.ROUTE: "core:route_detail",
        }[self.kind]
        return reverse(route, args=[self.target_id])


class SavedView(models.Model):
    """Сохранённый набор условий отбора для страницы системы."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_views",
        verbose_name=_("Пользователь"),
    )
    title = models.CharField(_("Название"), max_length=200)
    page = models.CharField(_("Страница"), max_length=64)
    query = models.CharField(_("Условия отбора"), max_length=1000, blank=True, default="")
    description = models.CharField(_("Пояснение"), max_length=500, blank=True, default="")

    # Токен публикации позволяет поделиться настроенным видом по ссылке,
    # не открывая доступ к личному кабинету автора.
    share_token = models.CharField(_("Токен доступа"), max_length=32, blank=True, default="")
    is_public = models.BooleanField(_("Доступен по ссылке"), default=False)

    created_at = models.DateTimeField(_("Создан"), auto_now_add=True)
    last_opened = models.DateTimeField(_("Последнее открытие"), null=True, blank=True)
    open_count = models.IntegerField(_("Число открытий"), default=0)

    class Meta:
        db_table = "app_saved_view"
        ordering = ("-created_at",)
        verbose_name = _("Сохранённый вид")
        verbose_name_plural = _("Сохранённые виды")

    def __str__(self) -> str:
        return self.title

    def publish(self) -> str:
        """Открыть доступ по ссылке, выпустив токен."""
        if not self.share_token:
            self.share_token = secrets.token_urlsafe(16)[:32]
        self.is_public = True
        self.save(update_fields=["share_token", "is_public"])
        return self.share_token

    def unpublish(self) -> None:
        """Закрыть публичный доступ к виду."""
        self.is_public = False
        self.save(update_fields=["is_public"])

    def register_open(self) -> None:
        """Отметить факт открытия вида — для сортировки «часто используемые»."""
        self.open_count += 1
        self.last_opened = timezone.now()
        self.save(update_fields=["open_count", "last_opened"])

    @property
    def url(self) -> str:
        """Восстановить адрес страницы вместе с условиями отбора."""
        base = reverse(self.page) if ":" in self.page else reverse(f"core:{self.page}")
        return f"{base}?{self.query}" if self.query else base


class ComparisonSet(models.Model):
    """Набор округов или объектов, отобранных пользователем для сравнения."""

    class Kind(models.TextChoices):
        DISTRICT = "district", _("Округа")
        OBJECT = "object", _("Объекты инфраструктуры")
        ROUTE = "route", _("Грузовые маршруты")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comparisons",
        verbose_name=_("Пользователь"),
    )
    title = models.CharField(_("Название"), max_length=200)
    kind = models.CharField(
        _("Тип сравнения"), max_length=16, choices=Kind.choices, default=Kind.DISTRICT
    )
    # Идентификаторы хранятся строкой через запятую: набор небольшой (до
    # десяти элементов), а такой формат напрямую подставляется в адрес страницы.
    members = models.CharField(_("Состав набора"), max_length=500, default="")
    created_at = models.DateTimeField(_("Создан"), auto_now_add=True)

    class Meta:
        db_table = "app_comparison_set"
        ordering = ("-created_at",)
        verbose_name = _("Набор сравнения")
        verbose_name_plural = _("Наборы сравнения")

    def __str__(self) -> str:
        return self.title

    @property
    def member_ids(self) -> list[int]:
        """Разобрать состав набора в список идентификаторов."""
        return [int(x) for x in self.members.split(",") if x.strip().isdigit()]

    @property
    def size(self) -> int:
        """Число элементов в наборе."""
        return len(self.member_ids)


class ExportJob(models.Model):
    """Задание на формирование отчётного документа."""

    class Format(models.TextChoices):
        XLSX = "xlsx", _("Электронная таблица XLSX")
        DOCX = "docx", _("Документ Word DOCX")
        CSV = "csv", _("Таблица CSV")
        PDF = "pdf", _("Документ PDF")
        GEOJSON = "geojson", _("Слой GeoJSON")

    class Status(models.TextChoices):
        PENDING = "pending", _("В очереди")
        DONE = "done", _("Готов")
        FAILED = "failed", _("Ошибка")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exports",
        verbose_name=_("Пользователь"),
    )
    title = models.CharField(_("Наименование отчёта"), max_length=250)
    dataset = models.CharField(_("Набор данных"), max_length=64)
    fmt = models.CharField(_("Формат"), max_length=16, choices=Format.choices)
    query = models.CharField(_("Условия отбора"), max_length=1000, blank=True, default="")
    status = models.CharField(
        _("Состояние"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    file_name = models.CharField(_("Имя файла"), max_length=250, blank=True, default="")
    file_size = models.IntegerField(_("Размер, байт"), default=0)
    row_count = models.IntegerField(_("Число строк"), default=0)
    error_message = models.CharField(_("Ошибка"), max_length=500, blank=True, default="")
    created_at = models.DateTimeField(_("Создано"), auto_now_add=True)
    finished_at = models.DateTimeField(_("Завершено"), null=True, blank=True)

    class Meta:
        db_table = "app_export_job"
        ordering = ("-created_at",)
        verbose_name = _("Задание на выгрузку")
        verbose_name_plural = _("Центр выгрузок")

    def __str__(self) -> str:
        return f"{self.title} ({self.fmt})"

    @property
    def size_human(self) -> str:
        """Размер файла в удобочитаемом виде."""
        size = float(self.file_size)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if size < 1024 or unit == "ГБ":
                return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} ГБ"

    @property
    def is_ready(self) -> bool:
        """Файл сформирован и доступен для скачивания."""
        return self.status == self.Status.DONE and bool(self.file_name)


class IncidentSubscription(models.Model):
    """Подписка пользователя на дорожные события по заданным условиям."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name=_("Пользователь"),
    )
    district = models.ForeignKey(
        "core.District",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subscriptions",
        verbose_name=_("Округ"),
    )
    min_severity = models.SmallIntegerField(_("Минимальная серьёзность"), default=3)
    cargo_only = models.BooleanField(_("Только влияющие на грузовой транспорт"), default=True)
    is_active = models.BooleanField(_("Активна"), default=True)
    created_at = models.DateTimeField(_("Создана"), auto_now_add=True)

    class Meta:
        db_table = "app_incident_subscription"
        ordering = ("-created_at",)
        verbose_name = _("Подписка на инциденты")
        verbose_name_plural = _("Подписки на инциденты")

    def __str__(self) -> str:
        return f"{self.scope_label} · от {self.min_severity} балла"

    @property
    def scope_label(self) -> str:
        """Словесное описание условий подписки."""
        scope = self.district.short_name if self.district else _("Вся Москва")
        if self.cargo_only:
            return _("%(scope)s, события для грузового транспорта") % {"scope": scope}
        return str(scope)

    @property
    def url(self) -> str:
        """Перечень событий, отвечающих условиям подписки."""
        params = [f"severity={self.min_severity}", "state=open"]
        if self.district_id:
            params.append(f"district={self.district_id}")
        if self.cargo_only:
            params.append("cargo=1")
        return f"{reverse('core:incident_list')}?{'&'.join(params)}"

    @property
    def conditions(self) -> models.Q:
        """Условия подписки выражением отбора для базы.

        Здесь те же правила, что и в :meth:`matches`, но пригодные для
        запроса: перечень событий подписки строится выборкой, а разбор
        принесённой загрузкой пачки — перебором в памяти. Совпадение этих
        двух прочтений проверяется набором тестов.
        """
        query = models.Q(severity__gte=self.min_severity)
        if self.cargo_only:
            query &= models.Q(affects_cargo=True)
        if self.district_id:
            query &= models.Q(district_id=self.district_id) | models.Q(
                district__isnull=True, road__district_id=self.district_id
            )
        return query

    def matching_incidents(self):
        """Открытые события, отвечающие условиям подписки."""
        from core.models import TrafficIncident

        return TrafficIncident.objects.open().filter(self.conditions)

    def matches(self, incident) -> bool:
        """Проверить, подпадает ли инцидент под условия подписки.

        Округ события определён по его координате; участок сети привлекается
        как запасной признак — событие может относиться к магистрали, но
        не иметь координаты, по которой округ находится.
        """
        if not self.is_active or incident.severity < self.min_severity:
            return False
        if self.cargo_only and not incident.affects_cargo:
            return False
        # Подписка без указания округа действует на всю территорию города.
        if not self.district_id:
            return True
        district_id = incident.district_id or getattr(incident.road, "district_id", None)
        return district_id == self.district_id


class Notification(models.Model):
    """Уведомление, адресованное пользователю."""

    class Level(models.TextChoices):
        INFO = "info", _("Информация")
        WARNING = "warning", _("Предупреждение")
        ALERT = "alert", _("Тревога")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("Пользователь"),
    )
    level = models.CharField(_("Уровень"), max_length=16, choices=Level.choices, default=Level.INFO)
    title = models.CharField(_("Заголовок"), max_length=250)
    body = models.CharField(_("Текст"), max_length=1000, blank=True, default="")
    url = models.CharField(_("Ссылка"), max_length=500, blank=True, default="")
    is_read = models.BooleanField(_("Прочитано"), default=False)
    created_at = models.DateTimeField(_("Создано"), auto_now_add=True)

    class Meta:
        db_table = "app_notification"
        ordering = ("-created_at",)
        verbose_name = _("Уведомление")
        verbose_name_plural = _("Уведомления")

    def __str__(self) -> str:
        return self.title


class AuditEvent(models.Model):
    """Запись журнала действий пользователей.

    Журнал ведётся по значимым событиям: вход и выход, изменение данных,
    выгрузка отчётов, административные операции. Массовые обращения к
    страницам просмотра не фиксируются, чтобы журнал оставался обозримым.
    """

    class Action(models.TextChoices):
        LOGIN = "login", _("Вход в систему")
        LOGOUT = "logout", _("Выход из системы")
        CREATE = "create", _("Создание записи")
        UPDATE = "update", _("Изменение записи")
        DELETE = "delete", _("Удаление записи")
        EXPORT = "export", _("Выгрузка отчёта")
        IMPORT = "import", _("Загрузка данных")
        ADMIN = "admin", _("Административное действие")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name=_("Пользователь"),
    )
    action = models.CharField(_("Действие"), max_length=16, choices=Action.choices, db_index=True)
    entity = models.CharField(_("Объект"), max_length=64, blank=True, default="")
    entity_id = models.CharField(_("Идентификатор"), max_length=64, blank=True, default="")
    summary = models.CharField(_("Описание"), max_length=500, blank=True, default="")
    path = models.CharField(_("Адрес запроса"), max_length=300, blank=True, default="")
    ip_address = models.GenericIPAddressField(_("IP-адрес"), null=True, blank=True)
    request_id = models.CharField(_("Идентификатор запроса"), max_length=32, blank=True, default="")
    created_at = models.DateTimeField(_("Время"), auto_now_add=True, db_index=True)

    class Meta:
        db_table = "app_audit_event"
        ordering = ("-created_at",)
        verbose_name = _("Событие журнала")
        verbose_name_plural = _("Журнал действий")

    def __str__(self) -> str:
        who = self.user.username if self.user else "аноним"
        return f"{self.created_at:%d.%m.%Y %H:%M} · {who} · {self.get_action_display()}"


def profile_by_api_token(raw: str) -> UserProfile | None:
    """Найти профиль по предъявленному токену REST API.

    Возвращается профиль вместе с учётной записью: вызывающему нужны и
    пользователь, и его роль, а раздельные запросы за тем и другим
    выполнялись бы на каждое обращение к интерфейсу.
    """
    if not raw or not raw.startswith(API_TOKEN_PREFIX):
        return None
    return (
        UserProfile.objects.select_related("user")
        .filter(api_token_hash=hash_api_token(raw))
        .first()
    )


def profile_for(user: User) -> UserProfile | None:
    """Получить профиль пользователя, создав его при отсутствии.

    Функция защищает шаблоны и представления от исключения, если профиль не
    был создан сигналом (например, при загрузке пользователей из фикстуры).
    """
    if not user or not user.is_authenticated:
        return None
    profile, _created = UserProfile.objects.get_or_create(
        user=user,
        defaults={"role": Role.ADMIN if user.is_superuser else Role.VIEWER},
    )
    return profile
