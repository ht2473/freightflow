"""Наполнение файла локализации английскими соответствиями.

Django формирует ``django.po`` с пустыми переводами; этот сценарий проставляет
значения по словарю. Отдельный сценарий выбран вместо ручной правки файла
потому, что при добавлении новых строк в интерфейс достаточно дополнить
словарь и повторно выполнить ``makemessages`` — уже проставленные переводы
сохраняются, а незаполненными остаются только действительно новые строки.

Запуск:
    python tools/fill_translations.py
    python backend/manage.py compilemessages
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Соответствия «русская строка → английская». Термины предметной области
#: приведены к принятой в англоязычных источниках форме: округ — district,
#: грузопоток — freight flow, загруженность — congestion.
TRANSLATIONS: dict[str, str] = {
    # --- Разделы и навигация ------------------------------------------------
    "Главная": "Home",
    "Карта": "Map",
    "Инфраструктура": "Infrastructure",
    "Дорожная сеть": "Road network",
    "Грузопотоки": "Freight flows",
    "Аналитика": "Analytics",
    "Данные": "Data",
    "Материалы": "Publications",
    "Справка": "Help",
    "О системе": "About",
    "Разделы": "Sections",
    "Основные разделы": "Main sections",
    "Меню разделов": "Section menu",
    "Карта сайта": "Site map",
    "Кабинет": "Account",
    "Личный кабинет": "Personal account",
    "Панель администратора": "Administration console",
    "Содержание": "Contents",
    "Помощь": "Help",

    # --- Реестры и справочники ----------------------------------------------
    "Реестр объектов": "Facility register",
    "Реестр объектов инфраструктуры": "Infrastructure facility register",
    "Объект": "Facility",
    "Объект инфраструктуры": "Infrastructure facility",
    "Объекты инфраструктуры": "Infrastructure facilities",
    "Округ": "District",
    "Округа": "Districts",
    "Административный округ": "Administrative district",
    "Административные округа": "Administrative districts",
    "Тип объекта": "Facility type",
    "Типы объектов": "Facility types",
    "Тип объекта инфраструктуры": "Infrastructure facility type",
    "Типы объектов инфраструктуры": "Infrastructure facility types",
    "Категория груза": "Cargo category",
    "Категории грузов": "Cargo categories",
    "Категории перевозимых грузов": "Categories of transported cargo",
    "Участок": "Segment",
    "Участок дороги": "Road segment",
    "Участок дорожной сети": "Road network segment",
    "Участки сети": "Network segments",
    "Участки дорожной сети": "Road network segments",
    "Маршрут": "Route",
    "Грузовой маршрут": "Freight route",
    "Грузовые маршруты": "Freight routes",
    "Источник данных": "Data source",
    "Источники": "Sources",
    "Источники данных": "Data sources",
    "Справочники": "Reference data",
    "Реестр интеграций": "Integration register",

    # --- Мониторинг и события ------------------------------------------------
    "Дорожная обстановка": "Traffic conditions",
    "Замер дорожной обстановки": "Traffic condition reading",
    "Инциденты": "Incidents",
    "Дорожный инцидент": "Traffic incident",
    "Дорожные инциденты": "Traffic incidents",
    "Журнал дорожных событий": "Traffic incident log",
    "Дорожно-транспортное происшествие": "Road accident",
    "Дорожные работы": "Roadworks",
    "Ограничение движения": "Traffic restriction",
    "Погодные условия": "Weather conditions",
    "Массовое мероприятие": "Public event",
    "Прочее": "Other",
    "Серьёзность": "Severity",
    "Минимальная серьёзность": "Minimum severity",
    "Балл загруженности": "Congestion level",
    "Загруженность в динамике": "Congestion over time",
    "Влияет на грузовой транспорт": "Affects freight traffic",
    "Только влияющие на грузовой транспорт": "Affecting freight traffic only",
    "Есть инцидент": "Incident present",
    "Устранён": "Resolved",
    "Зарегистрирован": "Reported",
    "Время замера": "Reading time",
    "Плотность потока, ТС/км": "Traffic density, veh/km",
    "Магистрали под мониторингом": "Monitored arterials",
    "Магистрали, обстановка и происшествия": "Arterials, conditions and incidents",
    "Магистраль скоростного движения": "Expressway",
    "Магистраль общегородского значения": "City arterial",
    "Улица районного значения": "Local street",
    "Класс дороги": "Road class",

    # --- Аналитика ------------------------------------------------------------
    "Индекс нагрузки": "Load index",
    "Индекс логистической нагрузки": "Logistics load index",
    "Композитная оценка округов": "Composite district score",
    "Типология": "Typology",
    "Типология округов": "District typology",
    "Кластерный анализ": "Cluster analysis",
    "Прогноз": "Forecast",
    "Прогноз грузопотока": "Freight flow forecast",
    "Оценка на 6 месяцев": "Six-month projection",
    "Сравнение": "Comparison",
    "Сравнение округов": "District comparison",
    "Сопоставление профилей": "Profile comparison",
    "Сценарный расчёт": "Scenario modelling",
    "Моделирование «что если»": "What-if modelling",
    "Расчёт показателей": "Indicator calculation",
    "Расчётные показатели и прогнозы": "Calculated indicators and forecasts",
    "Методология": "Methodology",
    "Статистика": "Statistics",
    "Статистика грузопотоков": "Freight flow statistics",
    "Показатель грузопотока": "Freight flow record",
    "Объёмы по периодам": "Volumes by period",
    "Объёмы перевозок и маршрутная сеть": "Traffic volumes and route network",
    "Помесячный объём перевозок": "Monthly transport volume",
    "Коридоры ввоза и вывоза": "Inbound and outbound corridors",

    # --- Учётные записи и доступ ---------------------------------------------
    "Пользователь": "User",
    "Пользователи": "Users",
    "Пользователи и доступ": "Users and access",
    "Профиль": "Profile",
    "Профиль пользователя": "User profile",
    "Профили пользователей": "User profiles",
    "Роль": "Role",
    "Наблюдатель": "Viewer",
    "Аналитик": "Analyst",
    "Диспетчер": "Operator",
    "Администратор": "Administrator",
    "Вход в систему": "Sign in",
    "Выход из системы": "Sign out",
    "Войти": "Sign in",
    "Войдите": "sign in",
    "Регистрация": "Registration",
    "Зарегистрируйтесь": "register",
    "Создать учётную запись": "Create account",
    "Создание учётной записи для работы с личным кабинетом системы.":
        "Create an account to use the personal area of the system.",
    "Имя пользователя": "Username",
    "Пароль": "Password",
    "Повторите пароль": "Repeat password",
    "Требования к паролю": "Password requirements",
    "Что даёт регистрация": "What registration provides",
    "Фамилия": "Last name",
    "Имя": "First name",
    "Организация": "Organisation",
    "Должность": "Position",
    "Телефон": "Phone",
    "Адрес электронной почты": "Email address",
    "Демонстрационный доступ": "Demonstration access",
    "Учётные записи стенда": "Demonstration accounts",
    "Токен доступа": "Access token",
    "Токен REST API": "REST API token",
    "Токен выпущен": "Token issued",
    "Доступ к API": "API access",

    # --- Личный кабинет --------------------------------------------------------
    "Обзор": "Overview",
    "Избранное": "Favourites",
    "Закладка": "Bookmark",
    "Закладки на объекты, округа, участки сети и маршруты.":
        "Bookmarks for facilities, districts, network segments and routes.",
    "Избранное — закладки на объекты, округа и участки сети":
        "Favourites — bookmarks for facilities, districts and network segments",
    "Сохранённые виды": "Saved views",
    "Сохранённый вид": "Saved view",
    "Сохранённые виды — настроенные условия отбора":
        "Saved views — stored filter settings",
    "Наборы сравнения": "Comparison sets",
    "Набор сравнения": "Comparison set",
    "Состав набора": "Set contents",
    "Тип сравнения": "Comparison type",
    "Группы округов, объектов и маршрутов для сопоставительного анализа.":
        "Groups of districts, facilities and routes for comparative analysis.",
    "Центр выгрузок": "Export centre",
    "Выгрузка отчётов": "Report export",
    "Выгрузка отчёта": "Report export",
    "Задание на выгрузку": "Export job",
    "Выгрузить:": "Export:",
    "Выгрузка недоступна": "Export unavailable",
    "Выгрузка доступна с роли «Аналитик»": "Export requires the Analyst role",
    "Подписки": "Subscriptions",
    "Подписка на инциденты": "Incident subscription",
    "Подписки на инциденты": "Incident subscriptions",
    "Подписки на дорожные события выбранного округа":
        "Subscriptions to traffic incidents in a selected district",
    "Уведомление": "Notification",
    "Уведомления": "Notifications",
    "Уведомлять об инцидентах": "Notify about incidents",
    "Сообщения системы о событиях, подпадающих под ваши подписки.":
        "System messages about events matching your subscriptions.",
    "Журнал действий": "Activity log",
    "Хронология входов, изменений и выгрузок в разрезе вашей учётной записи.":
        "History of sign-ins, changes and exports for your account.",
    "Контактные сведения, роль и предпочтения интерфейса.":
        "Contact details, role and interface preferences.",
    "Состояние личного кабинета и последние действия в системе.":
        "Personal area summary and recent activity in the system.",

    # --- Панель администратора --------------------------------------------------
    "Обзор системы": "System overview",
    "Журнал аудита": "Audit log",
    "Событие журнала": "Log event",
    "Качество данных": "Data quality",
    "Загрузка данных": "Data loading",
    "Журнал загрузок": "Load log",
    "Журнал загрузок данных": "Data load log",
    "История обновления данных": "Data update history",
    "Состояние среды": "Environment status",
    "Обращения": "Enquiries",
    "Обращение": "Enquiry",
    "Обратная связь": "Feedback",
    "Обращение отправлено": "Enquiry sent",
    "Спасибо за обратную связь.": "Thank you for your feedback.",
    "Текст обращения": "Enquiry text",
    "Тема обращения": "Enquiry subject",
    "Вопросы и замечания": "Questions and remarks",
    "Замечание к данным": "Data remark",
    "Предложение по развитию": "Improvement suggestion",
    "Вопрос по доступу": "Access request",
    "Ключевые счётчики, состояние данных и активность пользователей.":
        "Key counters, data status and user activity.",
    "Параметры контура развёртывания и версии компонентов.":
        "Deployment environment settings and component versions.",
    "Сообщения из формы обратной связи и подготовка ответов.":
        "Messages from the feedback form and reply preparation.",
    "Аналитические обзоры портала: публикация, снятие, вынос на главную.":
        "Portal analytical reviews: publish, withdraw, feature on home page.",
    "Полный перечень разделов и страниц информационной системы.":
        "Complete list of sections and pages of the system.",
    "Сопоставление округов по составляющим индекса логистической нагрузки.":
        "Comparison of districts by the components of the logistics load index.",

    # --- Материалы -------------------------------------------------------------
    "Аналитический материал": "Analytical article",
    "Аналитические материалы": "Analytical articles",
    "Аналитические обзоры по логистике": "Analytical reviews on logistics",
    "Рубрика": "Category",
    "Рубрика материалов": "Article category",
    "Рубрики материалов": "Article categories",
    "Текст материала": "Article text",
    "Информационная страница": "Information page",
    "Информационные страницы": "Information pages",
    "Краткое содержание": "Summary",
    "Вводный текст": "Lead text",
    "Время чтения, мин": "Reading time, min",
    "Число просмотров": "View count",
    "Дата публикации": "Publication date",
    "Опубликован": "Published",
    "Опубликована": "Published",
    "На главной": "Featured",
    "Руководство пользователя системы": "System user guide",

    # --- Общие поля и элементы интерфейса ----------------------------------------
    "Наименование": "Name",
    "Название": "Title",
    "Заголовок": "Heading",
    "Описание": "Description",
    "Пояснение": "Explanation",
    "Код": "Code",
    "Идентификатор": "Identifier",
    "Идентификатор объекта": "Facility identifier",
    "Идентификатор запроса": "Request identifier",
    "Адрес": "Address",
    "Адресный код": "Address code",
    "Адрес запроса": "Request path",
    "IP-адрес": "IP address",
    "Координаты": "Coordinates",
    "Центр округа": "District centre",
    "Границы округа": "District boundaries",
    "Геометрия участка": "Segment geometry",
    "Геометрия маршрута": "Route geometry",
    "Аббревиатура": "Abbreviation",
    "Автор": "Author",
    "Ссылка": "Link",
    "Текст": "Text",
    "Ответ": "Reply",
    "Дата ответа": "Reply date",
    "Обработал": "Handled by",
    "Клиент": "Client",
    "Вложение": "Attachment",
    "Заметка": "Note",
    "Состояние": "State",
    "Статус": "Status",
    "Уровень": "Level",
    "Действие": "Action",
    "Время": "Time",
    "Создан": "Created",
    "Создана": "Created",
    "Создано": "Created",
    "Обновлён": "Updated",
    "Обновлена": "Updated",
    "Добавлено": "Added",
    "Поступило": "Received",
    "Отправлено": "Sent",
    "Прочитано": "Read",
    "Активен": "Active",
    "Активна": "Active",
    "Доступен по ссылке": "Shared by link",
    "Число открытий": "Open count",
    "Последнее открытие": "Last opened",
    "Порядок вывода": "Display order",
    "Страница": "Page",
    "Код страницы": "Page code",
    "Страницы списка": "List pages",
    "Условия отбора": "Filters",
    "Представление": "View",
    "Предыдущая страница": "Previous page",
    "Следующая страница": "Next page",
    "Вы находитесь здесь": "You are here",
    "Перейти к содержанию": "Skip to content",
    "Скрыть сообщение": "Dismiss message",
    "Формат": "Format",
    "Имя файла": "File name",
    "Размер, байт": "Size, bytes",
    "Число строк": "Row count",
    "Набор данных": "Dataset",
    "Наименование отчёта": "Report name",
    "Целевая таблица": "Target table",
    "Загружено записей": "Records loaded",
    "Отклонено записей": "Records rejected",
    "Сообщение об ошибке": "Error message",
    "Начало": "Start",
    "Окончание": "End",
    "Начало периода": "Period start",
    "Тип периода": "Period type",
    "Тип события": "Event type",
    "Тип источника": "Source type",
    "Тип маршрута": "Route type",
    "Тип": "Type",
    "Периодичность обновления": "Update frequency",
    "Год": "Year",
    "Квартал": "Quarter",
    "Месяц": "Month",
    "Неделя": "Week",
    "Сутки": "Day",
    "Ежечасно": "Hourly",
    "Ежедневно": "Daily",
    "Еженедельно": "Weekly",
    "Ежемесячно": "Monthly",
    "Ежеквартально": "Quarterly",
    "Направление": "Direction",
    "Ввоз": "Inbound",
    "Вывоз": "Outbound",
    "Транзит": "Transit",
    "Ввоз в город": "Inbound to the city",
    "Вывоз из города": "Outbound from the city",
    "Регион отправления": "Origin region",
    "Регион назначения": "Destination region",
    "Округ по умолчанию": "Default district",
    "Язык интерфейса": "Interface language",
    "Оформление": "Appearance",
    "Тёмное": "Dark",
    "Светлое": "Light",
    "Как в системе": "System default",
    "Оформление: тёмное": "Appearance: dark",
    "Оформление: светлое": "Appearance: light",
    "Оформление: как в системе": "Appearance: system default",

    # --- Числовые характеристики -------------------------------------------------
    "Площадь, км²": "Area, km²",
    "Площадь, м²": "Area, m²",
    "Численность населения, чел.": "Population",
    "Мощность хранения, т": "Storage capacity, t",
    "Режим работы": "Operating hours",
    "Протяжённость, км": "Length, km",
    "Число полос": "Lanes",
    "Разрешённая скорость, км/ч": "Speed limit, km/h",
    "Средняя скорость, км/ч": "Average speed, km/h",
    "Среднее время в пути, ч": "Average travel time, h",
    "Время проезда, мин": "Travel time, min",
    "Интенсивность, ТС/сут.": "Intensity, veh/day",
    "Объём, т": "Volume, t",
    "Число рейсов": "Trip count",
    "Класс опасности ADR": "ADR hazard class",
    "Классификатор с классами ADR": "Classifier with ADR classes",
    "Классификатор объектов инфраструктуры": "Infrastructure facility classifier",

    # --- Состояния и итоги ---------------------------------------------------------
    "Новое": "New",
    "В работе": "In progress",
    "Отвечено": "Answered",
    "Закрыто": "Closed",
    "В очереди": "Queued",
    "Выполняется": "Running",
    "Готов": "Ready",
    "Завершено": "Completed",
    "Успешно": "Successful",
    "С замечаниями": "With remarks",
    "Ошибка": "Failed",
    "Информация": "Information",
    "Предупреждение": "Warning",
    "Тревога": "Alert",
    "Создание записи": "Record created",
    "Изменение записи": "Record changed",
    "Удаление записи": "Record deleted",
    "Административное действие": "Administrative action",

    # --- Форматы выгрузки ------------------------------------------------------------
    "Электронная таблица XLSX": "XLSX spreadsheet",
    "Документ Word DOCX": "Word DOCX document",
    "Документ PDF": "PDF document",
    "Таблица CSV": "CSV table",
    "Выгрузка CSV": "CSV export",
    "Слой GeoJSON": "GeoJSON layer",

    # --- Источники --------------------------------------------------------------------
    "Портал открытых данных": "Open data portal",
    "Геоинформационный сервис": "Geographic information service",
    "Ручной ввод": "Manual entry",
    "Иное": "Other",
    "Программный интерфейс (API)": "Application programming interface (API)",
    "Программный интерфейс (REST API)": "REST application programming interface",
    "Программный интерфейс и примеры": "Programming interface and examples",
    "Источники, качество и программный доступ":
        "Sources, quality and programmatic access",

    # --- Заголовки и подзаголовки страниц ------------------------------------------------
    "Логистическая инфраструктура": "Logistics infrastructure",
    "Логистическая инфраструктура Москвы": "Logistics infrastructure of Moscow",
    "Оперативная сводка по городу": "Operational summary for the city",
    "Карта логистической инфраструктуры": "Logistics infrastructure map",
    "Интерактивная карта логистической инфраструктуры":
        "Interactive map of logistics infrastructure",
    "Реестр складов, терминалов и грузовых дворов":
        "Register of warehouses, terminals and freight yards",
    "Склады, терминалы, распределительные центры":
        "Warehouses, terminals, distribution centres",
    "Профили административных округов Москвы":
        "Profiles of Moscow administrative districts",
    "Справка по системе": "System help",
    "Материалы и обратная связь": "Publications and feedback",
    "логистика Москвы": "Moscow logistics",

    # --- Подзаголовки разделов ------------------------------------------------------------
    "Единая информационная среда мониторинга складских мощностей, грузовых маршрутов и дорожной обстановки Московского транспортного узла.":
        "A single environment for monitoring storage capacity, freight routes and road conditions across the Moscow transport hub.",
    "Пространственное распределение складских мощностей, магистралей и дорожных событий на территории Москвы.":
        "Spatial distribution of storage capacity, arterials and traffic incidents across Moscow.",
    "Складские комплексы, грузовые терминалы, распределительные центры и площадки временного размещения грузов на территории Москвы.":
        "Warehouse complexes, freight terminals, distribution centres and temporary cargo sites across Moscow.",
    "Сравнительные профили двенадцати округов Москвы: обеспеченность складскими мощностями, объёмы грузопотока и загруженность дорог.":
        "Comparative profiles of the twelve Moscow districts: storage provision, freight volumes and road congestion.",
    "Классификатор, по которому ведётся учёт объектов: от складских комплексов до весовых пунктов контроля.":
        "The classifier used to record facilities, from warehouse complexes to weight control points.",
    "Классификатор грузов с указанием класса опасности по ДОПОГ. Перевозка опасных грузов требует согласования маршрута и времени.":
        "Cargo classifier with ADR hazard classes. Transporting hazardous cargo requires route and timing approval.",
    "Магистрали и городские улицы, включённые в систему мониторинга грузового движения, с текущей оценкой загруженности.":
        "Arterials and city streets covered by freight traffic monitoring, with current congestion assessment.",
    "Загруженность улично-дорожной сети по последним замерам системы мониторинга. Шкала — от 0 (свободно) до 10 (движение остановлено).":
        "Road network congestion based on the latest monitoring readings. The scale runs from 0 (free flow) to 10 (traffic halted).",
    "Происшествия, ремонтные работы и ограничения движения, влияющие на прохождение грузового транспорта по территории города.":
        "Accidents, roadworks and traffic restrictions affecting freight movement across the city.",
    "Объёмы перевозок в разрезе периодов, округов, направлений и категорий грузов по данным ведомственных источников.":
        "Transport volumes by period, district, direction and cargo category, based on departmental sources.",
    "Транспортные коридоры ввоза, вывоза и транзита грузов через Московский транспортный узел.":
        "Transport corridors for inbound, outbound and transit cargo through the Moscow transport hub.",
    "Система консолидирует сведения из ведомственных информационных систем, открытых данных и результатов натурных обследований.":
        "The system consolidates data from departmental information systems, open data and field surveys.",
    "Хронология обновления сведений: время выполнения, объём загруженных записей и число отклонённых строк.":
        "Data update history: run time, number of records loaded and rows rejected.",
    "Состав исходных данных, правила их приведения к сопоставимому виду и формулы расчёта аналитических показателей.":
        "Source data composition, harmonisation rules and formulas used to calculate analytical indicators.",
    "Машиночитаемый доступ к справочникам, реестрам и аналитике системы. Спецификация публикуется в формате OpenAPI 3.":
        "Machine-readable access to reference data, registers and analytics. The specification is published as OpenAPI 3.",
    "Назначение разделов, порядок работы с реестрами и картой, возможности личного кабинета и выгрузки отчётов.":
        "Purpose of each section, working with registers and the map, personal area features and report export.",
    "Назначение, архитектура и состав информационной системы по логистической инфраструктуре города Москвы.":
        "Purpose, architecture and composition of the Moscow logistics infrastructure information system.",
    "Композитная оценка нагрузки на логистическую инфраструктуру округа по четырём взвешенным составляющим, приведённым к стобалльной шкале.":
        "A composite assessment of district logistics load from four weighted components on a hundred-point scale.",
    "Разбиение округов на однородные группы по стандартизованным показателям нагрузки методом k-средних.":
        "Grouping districts into homogeneous clusters by standardised load indicators using k-means.",
    "Оценка помесячного объёма перевозок на ближайший период по модели линейного тренда с аддитивной сезонной составляющей.":
        "Monthly transport volume projection based on a linear trend with an additive seasonal component.",
    "Моделирование последствий изменения объёма перевозок, складских мощностей и пропускной способности дорожной сети.":
        "Modelling the effect of changes in transport volume, storage capacity and road network throughput.",
    "Учётные записи, назначенные роли и состояние доступа. Роль определяет объём полномочий во всех разделах системы.":
        "Accounts, assigned roles and access status. The role determines permissions across all sections.",
    "Классификаторы, на которых строится учёт. Изменение справочника затрагивает все связанные записи, поэтому правки выполняются через штатную админку с контролем ссылочной целостности.":
        "The classifiers underpinning the records. Changing reference data affects all linked records, so edits are made through the built-in admin with referential integrity checks.",
    "Автоматические проверки полноты и согласованности сведений. Выявленные дефекты не блокируют работу системы, но снижают достоверность аналитических выводов.":
        "Automatic completeness and consistency checks. Detected defects do not block the system but reduce the reliability of analytical conclusions.",
    "Хронология обновления сведений из внешних источников. Запуск процедуры вручную доступен для активных источников.":
        "History of updates from external sources. Manual runs are available for active sources.",
    "Регистрация значимых событий: входы, изменения данных, выгрузки и административные операции.":
        "Recording of significant events: sign-ins, data changes, exports and administrative operations.",
    "Обзоры и методические публикации о состоянии логистической инфраструктуры Москвы, подготовленные на данных системы.":
        "Reviews and methodological publications on Moscow logistics infrastructure, prepared from system data.",
    "Замечания к данным, предложения по развитию системы и сообщения об ошибках. Все обращения рассматриваются администратором системы.":
        "Data remarks, improvement suggestions and error reports. All enquiries are reviewed by the system administrator.",
    "Настроенные условия отбора. Сохраняются параметры, а не данные: при открытии выборка выполняется заново.":
        "Stored filter settings. Parameters are saved rather than data: the selection is re-run each time the view is opened.",
    "Персональный токен для программного обращения к системе. Передаётся в заголовке Authorization.":
        "A personal token for programmatic access. Passed in the Authorization header.",

    # --- Элементы интерфейса страниц ------------------------------------------------------
    "Показать": "Apply",
    "Очистить": "Clear",
    "Сбросить": "Reset",
    "Рассчитать": "Calculate",
    "Читать": "Read",
    "Написать": "Write",
    "Открыть карту": "Open map",
    "Базовый вариант": "Baseline",
    "Сначала посмотреть справку": "Check the help first",
    "Возможно, ответ уже есть": "The answer may already be there",
    "Ключевое слово": "Keyword",
    "Поиск": "Search",
    "Маршрут, регион отправления или назначения": "Route, origin or destination region",
    "Все типы": "All types",
    "Все классы": "All classes",
    "Любой": "Any",
    "Любое": "Any",
    "Любая": "Any",
    "Открыто сейчас": "Currently open",
    "Только круглосуточные": "Round-the-clock only",
    "Слои карты": "Map layers",
    "В радиусе трёх километров": "Within three kilometres",
    "Индекс": "Index",
    "Балл": "Score",
    "Доля": "Share",
    "Доля от общего числа": "Share of the total",
    "Доля успешных": "Success rate",
    "Итог": "Result",
    "Лидер": "Leader",
    "Рейтинг": "Ranking",
    "Записей": "Records",
    "Требуют внимания": "Need attention",
    "Наибольший индекс": "Highest index",
    "Коэффициент детерминации": "Coefficient of determination",
    "Коэффициент вариации": "Coefficient of variation",
    "Наблюдений в ряду": "Observations in series",
    "Тренд, т": "Trend, t",
    "Как читать прогноз": "How to read the forecast",
    "Модель отклика": "Response model",
    "Результат моделирования": "Modelling result",
    "Индекс нагрузки при заданных условиях": "Load index under the given conditions",
    "О методе": "About the method",
    "Суммарная площадь": "Total area",
    "Структура по типам": "Breakdown by type",
    "Динамика": "Trend over time",
    "Две недели наблюдений": "Two weeks of observations",
    "Отсортировано по убыванию загруженности": "Sorted by descending congestion",
    "Параметры": "Parameters",
    "Базовый адрес": "Base URL",
    "По токену": "With a token",
    "Рост спроса на доставку или его снижение": "Increase or decrease in delivery demand",
    "Ввод новых объектов или вывод существующих": "Commissioning new facilities or withdrawing existing ones",
    "Реконструкция магистралей или их ограничение": "Reconstruction of arterials or their restriction",

    # --- Сообщения о пустом результате ---------------------------------------------------------
    "Недостаточно данных для расчёта.": "Not enough data to calculate.",
    "Недостаточно данных для кластеризации.": "Not enough data for clustering.",
    "Данные о грузопотоках отсутствуют.": "No freight flow data available.",
    "Данные мониторинга отсутствуют.": "No monitoring data available.",
    "Замеры отсутствуют.": "No readings available.",
    "Записи журнала отсутствуют.": "No log entries.",
    "Маршруты не найдены.": "No routes found.",
    "Участки не закреплены за округом.": "Segments are not assigned to a district.",
    "Под заданные условия отбора данных нет.": "No data matches the selected filters.",

    # --- Оценки состояния движения и серьёзности -------------------------------------------
    "Свободно": "Free flow",
    "Небольшие затруднения": "Minor delays",
    "Затруднённое движение": "Heavy traffic",
    "Пробки": "Congestion",
    "Движение парализовано": "Traffic halted",
    "Незначительный": "Negligible",
    "Умеренный": "Moderate",
    "Значительный": "Significant",
    "Серьёзный": "Severe",
    "Критический": "Critical",
    "нет замеров": "no readings",
    "нет данных": "no data",

    # --- Подписи показателей и разделов страниц ------------------------------------------
    "Объектов в системе": "Facilities in the system",
    "Участков сети": "Network segments",
    "Грузовых маршрутов": "Freight routes",
    "Административных округов": "Administrative districts",
    "Назначение системы": "Purpose of the system",
    "Архитектура": "Architecture",
    "Разграничение доступа": "Access control",
    "Открытость данных": "Data openness",
    "Разработка": "Development",
    "Версия системы": "System version",
    "Платформа": "Platform",
    "Хранилище": "Storage",
    "Состав данных": "Data composition",
    "Документы": "Documents",
    "Методология расчётов": "Calculation methodology",
    "Программный интерфейс": "Programming interface",
    "Руководство пользователя": "User guide",
    "Округа для сравнения": "Districts to compare",
    "Сравнить": "Compare",
    "Сравнить округа": "Compare districts",
    "Наименьший индекс": "Lowest index",
    "Показатель": "Indicator",
    "Объектов инфраструктуры": "Infrastructure facilities",
    "Загруженность сети": "Network congestion",
    "Дорожных событий": "Traffic incidents",
    "Складские мощности": "Storage capacity",
    "Грузопоток": "Freight flow",
    "Аварийность": "Incident rate",
    "Профиль округа": "District profile",
    "Весь город": "Whole city",
    "Горизонт прогноза": "Forecast horizon",
    "Помесячный прирост тренда": "Monthly trend increment",
    "Факт и прогноз": "Actual and forecast",
    "Объём перевозок, тонн": "Transport volume, tonnes",
    "Прогнозные значения": "Forecast values",
    "Прогноз, т": "Forecast, t",
    "Сезонная поправка": "Seasonal adjustment",
    "Наибольшая нагрузка": "Highest load",
    "Наименьшая нагрузка": "Lowest load",
    "Среднее по городу": "City average",
    "Ранжирование": "Ranking",
    "Индекс по округам": "Index by district",
    "Профиль составляющих": "Component profile",
    "Как устроен расчёт": "How the calculation works",
    "Подробнее о методике": "More about the method",
    "Как читать индекс": "How to read the index",
    "Среднее изменение индекса": "Average index change",
    "Округов с ростом нагрузки": "Districts with rising load",
    "Округов со снижением": "Districts with falling load",
    "Параметры сценария": "Scenario parameters",
    "Базовый индекс": "Baseline index",
    "Сценарный индекс": "Scenario index",
    "Изменение": "Change",
    "Число групп": "Number of clusters",
    "Скопировать": "Copy",
    "Спецификация": "Specification",
    "Интерактивная документация": "Interactive documentation",
    "Справочный перечень": "Reference list",
    "Основные конечные точки": "Main endpoints",
    "Назначение": "Purpose",
    "Пример запроса": "Example request",
    "Пример ответа": "Example response",
    "Авторизация": "Authorisation",
    "Управление токеном": "Token management",
    "Ограничения на частоту": "Rate limits",
    "Без авторизации": "Without authorisation",
    "Размер страницы": "Page size",
    "Формат геометрии": "Geometry format",
    "Разделы системы по теме": "Related sections",
    "Профили округов": "District profiles",
    "Ещё в рубрике": "More in this category",
    "Поиск по материалам": "Search publications",
    "Все рубрики": "All categories",
    "Категорий в классификаторе": "Categories in the classifier",
    "Опасных грузов": "Hazardous cargo",
    "Классификация": "Classification",
    "Класс опасности": "Hazard class",
    "Объём перевозок": "Transport volume",
    "Рейсов": "Trips",
    "О классификации опасных грузов": "About hazardous cargo classification",
    "Грузопоток за период": "Freight flow for the period",
    "Плотность населения": "Population density",
    "Помесячная динамика": "Monthly trend",
    "Грузопоток округа, тонн": "District freight flow, tonnes",
    "Крупнейшие по мощности": "Largest by capacity",
    "Объекты инфраструктуры округа": "District infrastructure facilities",
    "Мощность, т": "Capacity, t",
    "Объекты не зарегистрированы.": "No facilities registered.",
    "Состав по типам объектов": "Breakdown by facility type",
    "Нет данных.": "No data.",
    "Показать округ на карте": "Show district on the map",
    "Округов": "Districts",
    "Мощность хранения": "Storage capacity",
    "Протяжённость сети": "Network length",
    "Ранжирование по грузопотоку": "Ranked by freight flow",
    "Объектов": "Facilities",
    "Мощность": "Capacity",
    "Доля грузопотока": "Share of freight flow",
    "Дороги, км": "Roads, km",
    "Обстановка": "Conditions",
    "Площадь": "Area",
    "Население": "Population",
    "Итог загрузки": "Load result",
    "Источник": "Source",
    "Все источники": "All sources",
    "Всего запусков": "Total runs",
    "Загружено": "Loaded",
    "Ошибок": "Errors",
    "Длительность": "Duration",
    "Сообщение": "Message",
    "Представьтесь": "Your name",
    "Адрес для ответа": "Reply address",
    "Необязательно": "Optional",
    "Отправить обращение": "Send enquiry",
    "Как обрабатываются обращения": "How enquiries are handled",
    "Срок ответа": "Response time",
    "Обработано обращений": "Enquiries handled",
    "Кто отвечает": "Who replies",
    "Справка по работе с системой": "Help on using the system",
    "Как рассчитываются показатели": "How indicators are calculated",
    "Откуда берутся данные": "Where the data comes from",
    "Программный доступ к данным": "Programmatic data access",
    "Обращение принято": "Enquiry received",
    "На главную": "To the home page",
    "Все округа": "All districts",
    "Все категории": "All categories",
    "Все направления": "All directions",
    "Средняя загрузка рейса": "Average load per trip",
    "Изменение за период": "Change over the period",
    "Объём грузопотока, тонн": "Freight flow volume, tonnes",
    "Структура": "Structure",
    "Направления": "Directions",
    "Ввоз, вывоз и транзит": "Inbound, outbound and transit",
    "Объём": "Volume",
    "Средняя загрузка": "Average load",
    "Состояние улично-дорожной сети": "Road network status",
    "Перевезено грузов": "Cargo transported",
    "Открытых инцидентов": "Open incidents",
    "Направления перевозок": "Transport directions",
    "Нет данных по направлениям.": "No data by direction.",
    "Объекты по типам": "Facilities by type",
    "Округа с наибольшим грузопотоком": "Districts with the highest freight flow",
    "Наиболее загруженные участки": "Most congested segments",
    "Скорость": "Speed",
    "Замеры обстановки отсутствуют.": "No condition readings available.",
    "Журнал событий": "Incident log",
    "Последние дорожные инциденты": "Latest traffic incidents",
    "Инциденты не зарегистрированы.": "No incidents registered.",
    "Аналитические обзоры": "Analytical reviews",
    "Материалы ещё не опубликованы.": "No publications yet.",
    "Уровень серьёзности": "Severity level",
    "Грузовой транспорт": "Freight traffic",
    "Описание события": "Incident description",
    "Участок сети": "Network segment",
    "Окрестность события": "Incident vicinity",
    "Обстановка на участке": "Conditions on the segment",
    "Место события": "Incident location",
    "Другие события на участке": "Other incidents on the segment",
    "Открытые": "Open",
    "Устранённые": "Resolved",
    "Серьёзность не ниже": "Severity at least",
    "Только влияющие на грузовые перевозки": "Affecting freight transport only",
    "Найдено событий": "Incidents found",
    "Влияют на грузовой транспорт": "Affect freight traffic",
    "Всего за период": "Total for the period",
    "Событие": "Incident",
    "Зарегистрировано": "Registered",
    "События не найдены.": "No incidents found.",
    "Пространственный обзор": "Spatial overview",
    "Дорожные события": "Traffic incidents",
    "Округа с показателями": "Districts with indicators",
    "Что рядом с точкой": "What is nearby",
    "Радиус поиска, км": "Search radius, km",
    "Состав исходных данных": "Source data composition",
    "Шкала загруженности": "Congestion scale",
    "Композитный индекс логистической нагрузки": "Composite logistics load index",
    "Прогнозирование грузопотока": "Freight flow forecasting",
    "Сценарное моделирование": "Scenario modelling",
    "Ограничения применения": "Limits of application",
    "Полнота данных": "Data completeness",
    "Смежные разделы": "Related sections",
    "В избранное": "Add to favourites",
    "Показать на карте": "Show on the map",
    "Характеристики объекта": "Facility characteristics",
    "Удельная мощность": "Capacity per area",
    "Расположение": "Location",
    "Соседние объекты": "Neighbouring facilities",
    "Расстояние": "Distance",
    "Загруженность": "Congestion",
    "Открыть профиль округа": "Open district profile",
    "Тот же округ": "Same district",
    "Другие объекты": "Other facilities",
    "Название или адрес объекта": "Facility name or address",
    "Любой источник": "Any source",
    "Найдено объектов": "Facilities found",
    "Суммарная мощность": "Total capacity",
    "Показано на странице": "Shown on this page",
    "Текущая загруженность": "Current congestion",
    "Скорость потока": "Traffic speed",
    "Средний балл за период": "Average score for the period",
    "Пропускная способность": "Throughput",
    "Загруженность участка по времени": "Segment congestion over time",
    "Замеры обстановки по участку отсутствуют.": "No condition readings for this segment.",
    "Трасса участка": "Segment alignment",
    "Характеристики": "Characteristics",
    "Класс": "Class",
    "Протяжённость": "Length",
    "Разрешённая скорость": "Speed limit",
    "Инциденты на участке": "Incidents on the segment",
    "Название магистрали": "Arterial name",
    "Участков под мониторингом": "Monitored segments",
    "Суммарная протяжённость": "Total length",
    "Оперативная обстановка": "Current conditions",
    "Текущее состояние": "Current state",
    "Участки не найдены.": "No segments found.",
    "Время в пути": "Travel time",
    "Интенсивность": "Intensity",
    "Транспортная работа": "Transport work",
    "Трасса маршрута": "Route alignment",
    "Статистика перевозок": "Transport statistics",
    "Показатели по периодам": "Indicators by period",
    "Период": "Period",
    "Категория": "Category",
    "Статистика отсутствует.": "No statistics available.",
    "Структура груза": "Cargo structure",
    "Схожие маршруты": "Similar routes",
    "Маршрутов": "Routes",
    "Интенсивность движения": "Traffic intensity",
    "Обзор кабинета": "Account overview",
    "Служебные страницы": "Service pages",
    "Всего загрузок": "Total loads",
    "Состояние источника": "Source status",
    "Хронология": "Chronology",
    "Последние загрузки": "Latest loads",
    "Таблица": "Table",
    "Загрузки не выполнялись.": "No loads have been run.",
    "Сведения об источнике": "Source details",
    "Периодичность": "Frequency",
    "Записи по источнику": "Records from this source",
    "Участки дорог": "Road segments",
    "Показатели грузопотоков": "Freight flow records",
    "Замеры обстановки": "Condition readings",
    "Источников данных": "Data sources",
    "Методика обработки": "Processing method",
    "Реестр источников": "Source register",
    "Загрузок": "Loads",
    "Отклонено": "Rejected",
    "Последняя загрузка": "Last load",
    "Контроль полноты": "Completeness control",
    "Наполненность основных таблиц": "Population of main tables",
    "Вся территория города": "The whole city",
    "Средняя загруженность": "Average congestion",
    "Участков в сводке": "Segments in the summary",
    "Час максимальной нагрузки": "Peak hour",
    "Средняя скорость за неделю": "Average speed for the week",
    "Суточный профиль загруженности": "Daily congestion profile",
    "Распределение": "Distribution",
    "Участки по состояниям": "Segments by state",
    "Обстановка по участкам": "Conditions by segment",
    "Время проезда": "Travel time",
    "Плотность": "Density",
    "Замер": "Reading",
    "Оповещение о дорожных событиях в выбранном округе с заданным порогом серьёзности.":
        "Notification of traffic incidents in the selected district above a given severity threshold.",
}


# Дополнение словаря вынесено в отдельный файл, чтобы основной перечень
# оставался обозримым.
try:
    from translations_extra import EXTRA
except ImportError:  # запуск не из каталога tools
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from translations_extra import EXTRA

TRANSLATIONS.update(EXTRA)

try:
    from translations_prose import PROSE
except ImportError:  # pragma: no cover
    PROSE = {}

TRANSLATIONS.update(PROSE)


def main() -> int:
    """Проставить переводы в файле локализации."""
    po_path = Path(__file__).resolve().parent.parent / "backend/locale/en/LC_MESSAGES/django.po"
    if not po_path.exists():
        print(f"Файл не найден: {po_path}", file=sys.stderr)
        print("Выполните: python backend/manage.py makemessages -l en", file=sys.stderr)
        return 1

    text = po_path.read_text(encoding="utf-8")

    # Снятие пометки «неточно».
    #
    # При обновлении файла переводов msgmerge подбирает похожие прежние
    # строки и переносит на них старый перевод, помечая запись как fuzzy.
    # Подбор ведётся по написанию, а не по смыслу, поэтому результат бывает
    # ошибочным: «Небольшие затруднения» получали перевод «Highest index».
    # Такие записи очищаются и заполняются заново по словарю.
    lines = text.split("\n")
    cleaned: list[str] = []
    drop_next_msgstr = False
    for line in lines:
        if line.startswith("#, ") and "fuzzy" in line:
            drop_next_msgstr = True
            flags = [f for f in line[3:].split(", ") if f != "fuzzy"]
            if flags:
                cleaned.append("#, " + ", ".join(flags))
            continue
        if line.startswith("#| "):  # прежний вариант строки — не нужен
            continue
        if drop_next_msgstr and line.startswith("msgstr "):
            cleaned.append('msgstr ""')
            drop_next_msgstr = False
            continue
        if line.startswith("msgid "):
            drop_next_msgstr = drop_next_msgstr
        cleaned.append(line)
    text = "\n".join(cleaned)

    filled = 0
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        nonlocal filled
        source = match.group(1)
        current = match.group(2)
        if current:  # перевод уже проставлен — не затираем
            return match.group(0)
        target = TRANSLATIONS.get(source)
        if target is None:
            missing.append(source)
            return match.group(0)
        filled += 1
        return f'msgid "{source}"\nmsgstr "{target}"'

    text = re.sub(r'msgid "([^"]*)"\nmsgstr "([^"]*)"', replace, text)
    po_path.write_text(text, encoding="utf-8")

    total = filled + len(missing)
    share = filled / total * 100 if total else 0
    print(f"Проставлено переводов: {filled} из {total} ({share:.0f} %)")
    if missing:
        print(f"Без перевода осталось: {len(missing)}")
        for item in missing[:15]:
            print(f"    {item}")
        if len(missing) > 15:
            print(f"    … и ещё {len(missing) - 15}")
        print("\nДополните словарь TRANSLATIONS и выполните сценарий повторно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
