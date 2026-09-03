"""Доменные модели ИС «ГрузПоток».

Модели один в один отображаются на таблицы, описанные в ``db/001_schema.sql``:
совпадают имена таблиц, колонок, типы и ограничения целостности. Благодаря
этому база данных может быть развёрнута двумя эквивалентными способами —
миграциями Django или «сырым» SQL.

Структура модуля повторяет логику предметной области:

1. справочники — округа, типы объектов, категории грузов, источники данных;
2. объекты — инфраструктура, участки дорог, грузовые маршруты;
3. временные ряды и события — грузопотоки, обстановка, инциденты;
4. служебное — журнал загрузок ETL.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from geo import LineStringField, MultiPolygonField, PointField

from .choices import (
    HAZARD_CLASS_LABELS,
    DataOrigin,
    EtlStatus,
    FlowDirection,
    IncidentType,
    OsmElement,
    PeriodType,
    RoadClass,
    RouteType,
    SourceType,
    UpdateFrequency,
    congestion_state,
    severity_state,
)

# =============================================================================
#  1. СПРАВОЧНИКИ
# =============================================================================


class DistrictQuerySet(models.QuerySet):
    """Выборки справочника округов."""

    def with_geometry(self) -> DistrictQuerySet:
        """Выборка вместе с границами округа.

        Требуется там, где границы действительно нужны: слой карты,
        отнесение объекта к территории, расчёт площади. Во всех прочих
        случаях достаточно наименования и показателей.
        """
        return self.defer(None)


class DistrictManager(models.Manager.from_queryset(DistrictQuerySet)):
    """Менеджер справочника округов, исключающий границы из выборки.

    Граница округа — мультиполигон в десятки тысяч вершин: справочник из
    двенадцати записей занимает в текстовом представлении около четырёхсот
    килобайт, и разбор их при каждом обращении к справочнику обходится
    дороже всей остальной работы страницы. Границы нужны немногим
    потребителям, поэтому запрашиваются явно — методом ``with_geometry``.
    """

    def get_queryset(self) -> DistrictQuerySet:
        return super().get_queryset().defer("geom")


class District(models.Model):
    """Административный округ города Москвы."""

    name = models.CharField(_("Наименование"), max_length=120, unique=True)
    short_name = models.CharField(_("Аббревиатура"), max_length=16, unique=True)
    area_sq_km = models.DecimalField(
        _("Площадь, км²"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    population = models.IntegerField(
        _("Численность населения, чел."),
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    geom = MultiPolygonField(_("Границы округа"), null=True, blank=True)
    center = PointField(_("Центр округа"), null=True, blank=True)

    objects = DistrictManager()

    class Meta:
        db_table = "districts"
        ordering = ("id",)
        verbose_name = _("Административный округ")
        verbose_name_plural = _("Административные округа")

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("core:district_detail", args=[self.pk])

    @property
    def density(self) -> float | None:
        """Плотность населения, человек на квадратный километр."""
        if not self.population or not self.area_sq_km:
            return None
        return float(self.population) / float(self.area_sq_km)

    @property
    def map_center(self) -> tuple[float, float] | None:
        """Координаты для центрирования карты: центр округа либо его границ."""
        if self.center is not None:
            return self.center.lon, self.center.lat
        if self.geom is not None:
            return self.geom.centroid
        return None


class InfrastructureType(models.Model):
    """Тип объекта логистической инфраструктуры."""

    code = models.CharField(_("Код"), max_length=32, unique=True)
    name = models.CharField(_("Наименование"), max_length=120)
    description = models.TextField(_("Описание"), blank=True, default="")

    class Meta:
        db_table = "infrastructure_types"
        ordering = ("id",)
        verbose_name = _("Тип объекта инфраструктуры")
        verbose_name_plural = _("Типы объектов инфраструктуры")

    def __str__(self) -> str:
        return self.name


class CargoCategory(models.Model):
    """Категория перевозимого груза с указанием класса опасности ADR."""

    code = models.CharField(_("Код"), max_length=32, unique=True)
    name = models.CharField(_("Наименование"), max_length=120)
    hazard_class = models.SmallIntegerField(
        _("Класс опасности ADR"),
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(9)],
    )

    class Meta:
        db_table = "cargo_categories"
        ordering = ("id",)
        verbose_name = _("Категория груза")
        verbose_name_plural = _("Категории грузов")

    def __str__(self) -> str:
        return self.name

    @property
    def is_hazardous(self) -> bool:
        """Признак опасного груза, требующего особого режима перевозки."""
        return self.hazard_class > 0

    @property
    def hazard_label(self) -> str:
        """Расшифровка класса опасности."""
        return HAZARD_CLASS_LABELS.get(self.hazard_class, "Не классифицирован")


class DataSource(models.Model):
    """Внешний источник данных, интегрированный в систему."""

    code = models.CharField(_("Код"), max_length=32, unique=True)
    name = models.CharField(_("Наименование"), max_length=200)
    source_type = models.CharField(_("Тип источника"), max_length=32, choices=SourceType.choices)
    url = models.CharField(_("Адрес"), max_length=500, blank=True, default="")
    update_frequency = models.CharField(
        _("Периодичность обновления"),
        max_length=32,
        choices=UpdateFrequency.choices,
        blank=True,
        default="",
    )
    is_active = models.BooleanField(_("Активен"), default=True)

    class Meta:
        db_table = "data_sources"
        ordering = ("id",)
        verbose_name = _("Источник данных")
        verbose_name_plural = _("Источники данных")

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("core:source_detail", args=[self.pk])

    @property
    def last_run(self):
        """Последняя запись журнала загрузок по этому источнику."""
        return self.etl_runs.order_by("-started_at").first()


# =============================================================================
#  2. ОБЪЕКТЫ ПРЕДМЕТНОЙ ОБЛАСТИ
# =============================================================================


class InfrastructureObjectQuerySet(models.QuerySet):
    """Типовые выборки реестра объектов инфраструктуры."""

    def with_refs(self) -> InfrastructureObjectQuerySet:
        """Подтянуть справочники одним запросом (защита от N+1).

        Границы округа при этом исключаются. Соединение со справочником
        округов присоединяет к каждой строке выборки и колонку с границами —
        мультиполигон в десятки тысяч вершин, который разбирается заново для
        каждой записи. На реестре в тысячу объектов это давало десятки секунд
        на запрос при том, что сами границы в списках не используются.

        Контур объекта исключается по той же причине: он нужен на карточке
        объекта, а не в перечне.
        """
        return (
            self.select_related("type", "district", "source")
            .defer("district__geom", "footprint")
        )

    def with_footprint(self) -> InfrastructureObjectQuerySet:
        """Выборка вместе с контуром объекта — для карточки и выгрузки."""
        return self.select_related("type", "district", "source").defer("district__geom")

    def in_district(self, district_id: int | None) -> InfrastructureObjectQuerySet:
        return self.filter(district_id=district_id) if district_id else self

    def of_type(self, type_id: int | None) -> InfrastructureObjectQuerySet:
        return self.filter(type_id=type_id) if type_id else self

    def search(self, term: str | None) -> InfrastructureObjectQuerySet:
        """Поиск по наименованию и адресу без учёта регистра."""
        if not term:
            return self
        return self.filter(models.Q(name__icontains=term) | models.Q(address__icontains=term))

    def located(self) -> InfrastructureObjectQuerySet:
        """Только объекты с известными координатами — для слоёв карты."""
        return self.exclude(geom__isnull=True)


class InfrastructureObject(models.Model):
    """Точечный объект логистической инфраструктуры."""

    type = models.ForeignKey(
        InfrastructureType,
        on_delete=models.PROTECT,
        db_column="type_id",
        related_name="facilities",
        verbose_name=_("Тип объекта"),
    )
    district = models.ForeignKey(
        District,
        on_delete=models.PROTECT,
        db_column="district_id",
        related_name="facilities",
        verbose_name=_("Округ"),
    )
    name = models.CharField(_("Наименование"), max_length=200)
    address = models.CharField(_("Адрес"), max_length=300, blank=True, default="")
    capacity_tons = models.DecimalField(
        _("Мощность хранения, т"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    area_sq_m = models.DecimalField(
        _("Площадь, м²"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    capacity_origin = models.CharField(
        _("Происхождение мощности"),
        max_length=16,
        choices=DataOrigin.choices,
        blank=True,
        default="",
        help_text=_("Откуда получено значение мощности хранения"),
    )
    area_origin = models.CharField(
        _("Происхождение площади"),
        max_length=16,
        choices=DataOrigin.choices,
        blank=True,
        default="",
        help_text=_("Откуда получено значение площади"),
    )
    operating_hours = models.CharField(
        _("Режим работы"), max_length=64, blank=True, default=""
    )
    operator = models.CharField(
        _("Оператор"), max_length=200, blank=True, default="",
        help_text=_("Организация, эксплуатирующая объект"),
    )
    website = models.CharField(_("Сайт"), max_length=300, blank=True, default="")
    phone = models.CharField(_("Телефон"), max_length=64, blank=True, default="")

    geom = PointField(_("Координаты"), null=True, blank=True)
    # Контур объекта хранится отдельно от точки. Площадь, вычисленная по нему,
    # является измеренной величиной, тогда как у объекта, размеченного одной
    # точкой, площадь неизвестна. Различие существенно для расчёта
    # обеспеченности округов складскими мощностями.
    footprint = MultiPolygonField(_("Контур объекта"), null=True, blank=True)

    # --- Происхождение записи ------------------------------------------------
    osm_type = models.CharField(
        _("Тип элемента OSM"),
        max_length=10,
        choices=OsmElement.choices,
        blank=True,
        default="",
    )
    osm_id = models.BigIntegerField(_("Идентификатор OSM"), null=True, blank=True)
    classification_rule = models.CharField(
        _("Правило отнесения"),
        max_length=32,
        blank=True,
        default="",
        help_text=_("Обозначение правила, по которому объект отнесён к типу"),
    )
    source_updated_at = models.DateTimeField(
        _("Данные источника от"), null=True, blank=True,
        help_text=_("Момент выгрузки, из которой получена запись"),
    )

    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="facilities",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )
    created_at = models.DateTimeField(_("Создан"), default=timezone.now)
    updated_at = models.DateTimeField(_("Обновлён"), auto_now=True)

    objects = InfrastructureObjectQuerySet.as_manager()

    class Meta:
        db_table = "infrastructure_objects"
        ordering = ("name", "id")
        verbose_name = _("Объект инфраструктуры")
        verbose_name_plural = _("Объекты инфраструктуры")
        indexes = [
            models.Index(fields=["district"], name="idx_infra_district"),
            models.Index(fields=["type"], name="idx_infra_type"),
            models.Index(fields=["osm_type", "osm_id"], name="idx_infra_osm"),
        ]
        constraints = [
            # Ключ исходного элемента уникален: повторная загрузка обновляет
            # запись, а не создаёт вторую. Нумерация точек, линий и отношений
            # в OpenStreetMap независима, поэтому в ключ входит и разновидность.
            models.UniqueConstraint(
                fields=["osm_type", "osm_id"],
                condition=models.Q(osm_id__isnull=False),
                name="uniq_infra_osm_element",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("core:object_detail", args=[self.pk])

    @property
    def is_round_the_clock(self) -> bool:
        """Признак круглосуточного режима работы."""
        hours = (self.operating_hours or "").lower()
        return "кругл" in hours or hours == "00:00-24:00"

    @property
    def utilization_hint(self) -> float | None:
        """Удельная мощность объекта — тонн на квадратный метр площади.

        Косвенно характеризует технологию хранения: значения выше единицы
        свойственны многоярусным стеллажным комплексам, ниже 0,2 — открытым
        площадкам и контейнерным терминалам.
        """
        if not self.capacity_tons or not self.area_sq_m:
            return None
        return float(self.capacity_tons) / float(self.area_sq_m)


class RoadSegment(models.Model):
    """Участок улично-дорожной сети, включённый в мониторинг."""

    name = models.CharField(_("Наименование"), max_length=200)
    road_class = models.CharField(_("Класс дороги"), max_length=32, choices=RoadClass.choices)
    lanes = models.SmallIntegerField(
        _("Число полос"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(16)],
    )
    length_km = models.DecimalField(
        _("Протяжённость, км"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    speed_limit_kmh = models.SmallIntegerField(
        _("Разрешённая скорость, км/ч"),
        null=True,
        blank=True,
        validators=[MinValueValidator(5), MaxValueValidator(130)],
    )
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        db_column="district_id",
        related_name="roads",
        null=True,
        blank=True,
        verbose_name=_("Округ"),
    )
    geom = LineStringField(_("Геометрия участка"), null=True, blank=True)
    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="roads",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )

    class Meta:
        db_table = "road_segments"
        ordering = ("name", "id")
        verbose_name = _("Участок дорожной сети")
        verbose_name_plural = _("Участки дорожной сети")

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("core:road_detail", args=[self.pk])

    @property
    def latest_condition(self) -> TrafficCondition | None:
        """Последний по времени замер обстановки на участке."""
        return self.conditions.order_by("-recorded_at").first()

    @property
    def capacity_index(self) -> float | None:
        """Условная пропускная способность участка.

        Произведение числа полос на разрешённую скорость — грубая, но
        сопоставимая между участками оценка провозной возможности.
        """
        if not self.lanes or not self.speed_limit_kmh:
            return None
        return float(self.lanes * self.speed_limit_kmh)


class CargoRoute(models.Model):
    """Грузовой маршрут (транспортный коридор)."""

    name = models.CharField(_("Наименование"), max_length=200)
    route_type = models.CharField(_("Тип маршрута"), max_length=16, choices=RouteType.choices)
    origin_region = models.CharField(
        _("Регион отправления"), max_length=120, blank=True, default=""
    )
    destination = models.CharField(
        _("Регион назначения"), max_length=120, blank=True, default=""
    )
    distance_km = models.DecimalField(
        _("Протяжённость, км"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    avg_duration_h = models.DecimalField(
        _("Среднее время в пути, ч"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    truck_count_day = models.IntegerField(
        _("Интенсивность, ТС/сут."),
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    geom = LineStringField(_("Геометрия маршрута"), null=True, blank=True)
    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="routes",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )

    class Meta:
        db_table = "cargo_routes"
        ordering = ("name", "id")
        verbose_name = _("Грузовой маршрут")
        verbose_name_plural = _("Грузовые маршруты")

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("core:route_detail", args=[self.pk])

    @property
    def avg_speed_kmh(self) -> float | None:
        """Средняя скорость доставки по маршруту, км/ч."""
        if not self.distance_km or not self.avg_duration_h:
            return None
        duration = float(self.avg_duration_h)
        return float(self.distance_km) / duration if duration else None

    @property
    def daily_load_index(self) -> float | None:
        """Суточная транспортная работа маршрута, ТС·км.

        Показатель позволяет сопоставлять короткие интенсивные маршруты с
        протяжёнными, но менее загруженными коридорами.
        """
        if not self.truck_count_day or not self.distance_km:
            return None
        return float(self.truck_count_day) * float(self.distance_km)


# =============================================================================
#  3. ВРЕМЕННЫЕ РЯДЫ И СОБЫТИЯ
# =============================================================================


class FreightFlowStat(models.Model):
    """Агрегированный показатель грузопотока за период."""

    period_date = models.DateField(_("Начало периода"))
    period_type = models.CharField(
        _("Тип периода"), max_length=16, choices=PeriodType.choices, default=PeriodType.MONTH
    )
    route = models.ForeignKey(
        CargoRoute,
        on_delete=models.SET_NULL,
        db_column="route_id",
        related_name="flow_stats",
        null=True,
        blank=True,
        verbose_name=_("Маршрут"),
    )
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        db_column="district_id",
        related_name="flow_stats",
        null=True,
        blank=True,
        verbose_name=_("Округ"),
    )
    cargo_category = models.ForeignKey(
        CargoCategory,
        on_delete=models.SET_NULL,
        db_column="cargo_cat_id",
        related_name="flow_stats",
        null=True,
        blank=True,
        verbose_name=_("Категория груза"),
    )
    direction = models.CharField(_("Направление"), max_length=16, choices=FlowDirection.choices)
    volume_tons = models.DecimalField(
        _("Объём, т"), max_digits=14, decimal_places=2, null=True, blank=True
    )
    vehicle_count = models.IntegerField(_("Число рейсов"), null=True, blank=True)
    avg_speed_kmh = models.DecimalField(
        _("Средняя скорость, км/ч"), max_digits=6, decimal_places=2, null=True, blank=True
    )
    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="flow_stats",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )

    class Meta:
        db_table = "freight_flow_stats"
        ordering = ("-period_date", "id")
        verbose_name = _("Показатель грузопотока")
        verbose_name_plural = _("Статистика грузопотоков")
        indexes = [
            models.Index(fields=["period_date"], name="idx_flow_period"),
            models.Index(fields=["district", "period_date"], name="idx_flow_district"),
        ]

    def __str__(self) -> str:
        return f"{self.period_date:%Y-%m} · {self.get_direction_display()}"

    @property
    def avg_load_per_vehicle(self) -> float | None:
        """Средняя загрузка одного транспортного средства, тонн."""
        if not self.volume_tons or not self.vehicle_count:
            return None
        return float(self.volume_tons) / self.vehicle_count


class TrafficCondition(models.Model):
    """Замер дорожной обстановки на участке сети."""

    recorded_at = models.DateTimeField(_("Время замера"))
    road = models.ForeignKey(
        RoadSegment,
        on_delete=models.CASCADE,
        db_column="road_id",
        related_name="conditions",
        verbose_name=_("Участок"),
    )
    congestion_level = models.SmallIntegerField(
        _("Балл загруженности"),
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    avg_speed_kmh = models.DecimalField(
        _("Средняя скорость, км/ч"), max_digits=6, decimal_places=2, null=True, blank=True
    )
    travel_time_min = models.DecimalField(
        _("Время проезда, мин"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    vehicle_density = models.IntegerField(_("Плотность потока, ТС/км"), null=True, blank=True)
    incident_flag = models.BooleanField(_("Есть инцидент"), default=False)
    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="traffic_records",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )

    class Meta:
        db_table = "traffic_conditions"
        ordering = ("-recorded_at", "id")
        verbose_name = _("Замер дорожной обстановки")
        verbose_name_plural = _("Дорожная обстановка")
        indexes = [
            models.Index(fields=["road", "-recorded_at"], name="idx_traffic_road_time"),
            models.Index(fields=["-recorded_at"], name="idx_traffic_time"),
        ]

    def __str__(self) -> str:
        return f"{self.road_id} · {self.recorded_at:%d.%m %H:%M} · {self.congestion_level}"

    @property
    def state(self) -> tuple[str, str, str]:
        """Кортеж ``(код, подпись, тон)`` для отображения состояния движения."""
        return congestion_state(self.congestion_level)

    @property
    def speed_ratio(self) -> float | None:
        """Отношение фактической скорости к разрешённой на участке.

        Значение около единицы означает свободное движение, ниже 0,4 —
        затор. Показатель нормирован и потому сравним между участками с
        разными скоростными режимами.
        """
        limit = self.road.speed_limit_kmh if self.road_id else None
        if not limit or self.avg_speed_kmh is None:
            return None
        return float(self.avg_speed_kmh) / float(limit)


class TrafficIncidentQuerySet(models.QuerySet):
    """Типовые выборки журнала инцидентов."""

    def open(self) -> TrafficIncidentQuerySet:
        """Незакрытые инциденты — основа оперативной сводки."""
        return self.filter(resolved_at__isnull=True)

    def affecting_cargo(self) -> TrafficIncidentQuerySet:
        """События, влияющие на движение грузового транспорта."""
        return self.filter(affects_cargo=True)

    def with_refs(self) -> TrafficIncidentQuerySet:
        return self.select_related("road", "road__district", "source").defer(
            "road__district__geom"
        )


class TrafficIncident(models.Model):
    """Событие на улично-дорожной сети."""

    reported_at = models.DateTimeField(_("Зарегистрирован"))
    resolved_at = models.DateTimeField(_("Устранён"), null=True, blank=True)
    incident_type = models.CharField(_("Тип события"), max_length=32, choices=IncidentType.choices)
    severity = models.SmallIntegerField(
        _("Серьёзность"),
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    road = models.ForeignKey(
        RoadSegment,
        on_delete=models.SET_NULL,
        db_column="road_id",
        related_name="incidents",
        null=True,
        blank=True,
        verbose_name=_("Участок"),
    )
    description = models.TextField(_("Описание"), blank=True, default="")
    geom = PointField(_("Координаты"), null=True, blank=True)
    affects_cargo = models.BooleanField(_("Влияет на грузовой транспорт"), default=False)
    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="incidents",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )

    objects = TrafficIncidentQuerySet.as_manager()

    class Meta:
        db_table = "traffic_incidents"
        ordering = ("-reported_at", "id")
        verbose_name = _("Дорожный инцидент")
        verbose_name_plural = _("Дорожные инциденты")
        indexes = [models.Index(fields=["-reported_at"], name="idx_incidents_time")]

    def __str__(self) -> str:
        return f"{self.get_incident_type_display()} · {self.reported_at:%d.%m.%Y %H:%M}"

    def get_absolute_url(self) -> str:
        return reverse("core:incident_detail", args=[self.pk])

    @property
    def is_open(self) -> bool:
        """Инцидент не закрыт и продолжает влиять на движение."""
        return self.resolved_at is None

    @property
    def duration_hours(self) -> float | None:
        """Длительность инцидента в часах; для открытых — с момента регистрации."""
        end = self.resolved_at or timezone.now()
        return (end - self.reported_at).total_seconds() / 3600

    @property
    def severity_state(self) -> tuple[str, str]:
        """Подпись и тон оформления уровня серьёзности."""
        return severity_state(self.severity)


# =============================================================================
#  4. СЛУЖЕБНЫЕ СУЩНОСТИ
# =============================================================================


class EtlRun(models.Model):
    """Запись журнала загрузки данных из внешнего источника."""

    started_at = models.DateTimeField(_("Начало"), default=timezone.now)
    finished_at = models.DateTimeField(_("Окончание"), null=True, blank=True)
    source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        db_column="source_id",
        related_name="etl_runs",
        null=True,
        blank=True,
        verbose_name=_("Источник данных"),
    )
    target_table = models.CharField(_("Целевая таблица"), max_length=64)
    records_loaded = models.IntegerField(_("Загружено записей"), default=0)
    records_errors = models.IntegerField(_("Отклонено записей"), default=0)
    status = models.CharField(
        _("Статус"), max_length=16, choices=EtlStatus.choices, default=EtlStatus.RUNNING
    )
    error_message = models.TextField(_("Сообщение об ошибке"), blank=True, default="")

    class Meta:
        db_table = "etl_log"
        ordering = ("-started_at", "id")
        verbose_name = _("Загрузка данных")
        verbose_name_plural = _("Журнал загрузок данных")

    def __str__(self) -> str:
        return f"{self.target_table} · {self.started_at:%d.%m.%Y %H:%M} · {self.status}"

    @property
    def duration_minutes(self) -> float | None:
        """Продолжительность загрузки в минутах."""
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds() / 60

    #: Понятные наименования разделов, наполняемых процедурой загрузки.
    #: Пользователю показывается наименование, а не имя таблицы в базе.
    TARGET_LABELS = {
        "districts": _("Административные округа"),
        "infrastructure_types": _("Типы объектов"),
        "cargo_categories": _("Категории грузов"),
        "data_sources": _("Источники данных"),
        "infrastructure_objects": _("Объекты инфраструктуры"),
        "road_segments": _("Участки дорожной сети"),
        "cargo_routes": _("Грузовые маршруты"),
        "traffic_conditions": _("Замеры обстановки"),
        "traffic_incidents": _("Дорожные инциденты"),
        "freight_flow_stats": _("Показатели грузопотоков"),
        "etl_log": _("Журнал загрузок"),
    }

    @property
    def target_label(self) -> str:
        """Наименование раздела, наполненного загрузкой.

        Записи вида ``seed:002_seed_data_scale1.sql`` относятся к загрузке
        поставляемого набора целиком; для них выводится обобщённое название,
        поскольку имя файла ничего не сообщает пользователю.
        """
        if self.target_table.startswith("seed:"):
            return str(_("Полная загрузка набора"))
        label = self.TARGET_LABELS.get(self.target_table)
        return str(label) if label else self.target_table

    @property
    def error_rate(self) -> float:
        """Доля отклонённых записей от общего числа обработанных, %."""
        total = self.records_loaded + self.records_errors
        return (self.records_errors / total * 100) if total else 0.0
