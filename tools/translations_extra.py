"""Дополнение словаря переводов интерфейса.

Файл подключается сценарием ``tools/fill_translations.py`` и содержит
соответствия, добавленные при завершении перевода интерфейса. Вынесен
отдельно, чтобы основной словарь оставался обозримым.
"""

from __future__ import annotations

EXTRA: dict[str, str] = {
    # --- Журнал действий -----------------------------------------------------
    "Изменение профиля": "Profile updated",
    "Изменение избранного": "Favourites changed",
    "Сохранение вида": "View saved",
    "Действие с сохранённым видом": "Saved view action",
    "Оформление подписки": "Subscription created",
    "Отмена подписки": "Subscription cancelled",
    "Уведомления отмечены прочитанными": "Notifications marked as read",
    "Управление токеном доступа": "Access token management",
    "Смена пароля": "Password changed",
    "Отправка обращения": "Enquiry submitted",
    "Изменение учётной записи": "Account changed",
    "Публикация материала": "Article publication",
    "Обработка обращения": "Enquiry handled",
    "Сброс кеша сводок": "Summary cache cleared",
    "Изменение данных": "Data changed",
    "Полная загрузка набора": "Full dataset load",
    "Тип действия": "Action type",
    "Все действия": "All actions",
    "Последние действия": "Recent activity",
    "Действия не зафиксированы.": "No activity recorded.",
    "Весь журнал →": "Full log →",
    "Журнал моих действий": "My activity log",
    "В журнал попадают входы в систему, изменения данных, выгрузки отчётов и административные операции. Обращения к страницам просмотра не фиксируются.":
        "The log records sign-ins, data changes, report exports and administrative operations. Page views are not recorded.",

    # --- Доступ к программному интерфейсу ------------------------------------
    "Персональный токен": "Personal token",
    "Описание интерфейса": "Interface description",
    "Выпустить новый": "Issue a new one",
    "Отозвать": "Revoke",
    "Выпустить токен": "Issue token",
    "Пример обращения": "Example request",
    "Базовый адрес:": "Base URL:",
    "Ограничения": "Limits",
    "Без токена": "Without a token",
    "60 запросов/мин": "60 requests/min",
    "600 запросов/мин": "600 requests/min",
    "до 500 записей": "up to 500 records",
    "Документация": "Documentation",
    "Справочная страница API": "API reference page",
    "Спецификация OpenAPI": "OpenAPI specification",
    "Токен не выпущен. Он потребуется для обращений к API с повышенной частотой и для доступа к методам, требующим авторизации.":
        "No token has been issued. It is required for higher request rates and for methods that need authorisation.",
    "машиночитаемое описание всех методов": "machine-readable description of all methods",
    "Authorization: Token ВАШ_ТОКЕН": "Authorization: Token YOUR_TOKEN",
    "60 запросов в минуту": "60 requests per minute",
    "600 запросов в минуту": "600 requests per minute",
    "При превышении возвращается код 429 и заголовок Retry-After с числом секунд до следующей попытки.":
        "Exceeding the limit returns code 429 and a Retry-After header with the number of seconds until the next attempt.",

    # --- Центр выгрузок -------------------------------------------------------
    "Всего выгрузок": "Total exports",
    "Суммарный объём": "Total size",
    "Выгружено строк": "Rows exported",
    "Право на выгрузку": "Export permission",
    "Выгрузка отчётов недоступна": "Report export unavailable",
    "Отчёт": "Report",
    "Строк": "Rows",
    "Размер": "Size",
    "готов": "ready",
    "ошибка": "failed",
    "в очереди": "queued",
    "Скачать": "Download",
    "Выгрузки": "Exports",
    "Элементов в наборе": "Items in the set",
    "формой обратной связи": "the feedback form",
    "формуобратной связи": "the feedback form",
    "формуобратнойсвязи": "the feedback form",
    "форму обратной связи": "the feedback form",
    ", указав организацию и характер задач.":
        ", stating your organisation and the nature of your work.",
    ", указав организацию и характер выполняемых задач.":
        ", stating your organisation and the nature of your work.",
    "Формирование отчётов в форматах XLSX, DOCX, CSV, PDF и GeoJSON предоставляется с роли «Аналитик». Запросите расширение прав через":
        "Report generation in XLSX, DOCX, CSV, PDF and GeoJSON is available from the Analyst role. Request extended permissions through",

    # --- Личный кабинет ---------------------------------------------------------
    "Тип закладки": "Bookmark type",
    "Убрать": "Remove",
    "Все": "All",
    "Непрочитанные": "Unread",
    "Отметить все прочитанными": "Mark all as read",
    "Перейти": "Open",
    "Учётная запись": "Account",
    "Электронная почта": "Email",
    "Последний вход": "Last sign-in",
    "Изменить профиль": "Edit profile",
    "Сменить пароль": "Change password",
    "Все →": "All →",
    "Новые уведомления": "New notifications",
    "Все уведомления": "All notifications",
    "Открыть панель": "Open console",
    "Личные сведения": "Personal details",
    "Место работы": "Place of work",
    "Предпочтения интерфейса": "Interface preferences",
    "Сохранить изменения": "Save changes",
    "Текущая роль": "Current role",
    "Доступ": "Access",
    "Безопасность": "Security",
    "по ссылке": "by link",
    "Открыть": "Open",
    "Закрыть доступ": "Revoke link",
    "Открыть по ссылке": "Share by link",
    "Удалить": "Delete",
    "Сохранить новый вид": "Save a new view",
    "Сохранить": "Save",
    "Территория": "Area",
    "Порог серьёзности": "Severity threshold",
    "Только грузовые": "Freight only",
    "активна": "active",
    "отключена": "disabled",
    "Новая подписка": "New subscription",
    "Подписаться": "Subscribe",
    "Шкала серьёзности": "Severity scale",
    "1 — незначительный": "1 — negligible",
    "помеха движению": "an obstruction to traffic",
    "2 — умеренный": "2 — moderate",
    "сужение проезжей части": "carriageway narrowing",
    "3 — значительный": "3 — significant",
    "перекрытие полосы": "lane closure",
    "4 — серьёзный": "4 — severe",
    "перекрытие направления": "direction closure",
    "5 — критический": "5 — critical",
    "полное перекрытие": "full closure",
    "Управление пользователями, справочниками и контроль качества данных.":
        "User management, reference data and data quality control.",
    "Уведомлять о дорожных событиях по моим подпискам":
        "Notify me about traffic incidents matching my subscriptions",
    "Роль назначает администратор системы. Для расширения полномочий направьте запрос через":
        "The role is assigned by the system administrator. To request extended permissions, write through",
    "Сохраняются условия отбора, а не сами данные: при открытии выборка выполняется заново и показывает актуальные значения.":
        "Filter settings are saved rather than the data itself: the selection is re-run on opening and shows current values.",
    "Только события, влияющие на грузовой транспорт":
        "Only incidents affecting freight traffic",
    "Избранное, сохранённые виды, выгрузки и подписки":
        "Favourites, saved views, exports and subscriptions",

    # --- Панель администратора -------------------------------------------------------
    "Разработчик:": "Developer:",
    "Все пользователи": "All users",
    "7 суток": "7 days",
    "30 суток": "30 days",
    "90 суток": "90 days",
    "Событий за период": "Events in the period",
    "Распределение по типам": "Breakdown by type",
    "Запрос": "Request",
    "Событий за период нет.": "No events in the period.",
    "Опубликовано": "Published",
    "Черновиков": "Drafts",
    "Просмотров всего": "Total views",
    "Рубрик": "Categories",
    "Создать материал →": "Create an article →",
    "Состояние публикации": "Publication state",
    "Все материалы": "All articles",
    "Опубликованные": "Published",
    "Черновики": "Drafts",
    "Материал": "Article",
    "Публикация": "Publication",
    "Просмотров": "Views",
    "Действия": "Actions",
    "опубликован": "published",
    "черновик": "draft",
    "на главной": "featured",
    "Снять": "Withdraw",
    "Опубликовать": "Publish",
    "Материалов нет.": "No articles.",
    "Пользователей": "Users",
    "Новых обращений": "New enquiries",
    "Успешность загрузок": "Load success rate",
    "Наполнение": "Population",
    "Объёмы основных таблиц": "Size of the main tables",
    "Качество данных →": "Data quality →",
    "Геокодирование": "Geocoding",
    "не применяется": "not applicable",
    "Аудит": "Audit",
    "Последние действия пользователей": "Recent user activity",
    "Событий нет.": "No events.",
    "Пользователи по ролям": "Users by role",
    "Управление доступом": "Access management",
    "Содержание портала": "Portal content",
    "Опубликовано материалов": "Articles published",
    "Выгрузок за неделю": "Exports this week",
    "Обслуживание": "Maintenance",
    "Сбросить кеш": "Clear cache",
    "Запусков всего": "Total runs",
    "Доля ошибок": "Error share",
    "Запуск загрузки": "Running a load",
    "Сбросить кеш сводок": "Clear summary cache",
    "Тема": "Subject",
    "Все темы": "All subjects",
    "Возраст": "Age",
    "Обращений нет.": "No enquiries.",
    "Вложение:": "Attachment:",
    "Подготовить ответ": "Prepare a reply",
    "Текст ответа": "Reply text",
    "Отправить ответ": "Send reply",
    "Взять в работу": "Take into work",
    "Закрыть без ответа": "Close without reply",
    "Автор обращения": "Enquirer",
    "Представился": "Gave name",
    "Адрес почты": "Email address",
    "К списку обращений": "Back to enquiries",
    "Выявлено замечаний": "Findings",
    "Проверок выполняется": "Checks performed",
    "при каждом открытии раздела": "each time the section is opened",
    "Методика": "Method",
    "Раздел «Методология» →": "Methodology section →",
    "Автоматические проверки": "Automatic checks",
    "Полнота и согласованность данных": "Data completeness and consistency",
    "Как трактовать результаты": "How to interpret the results",
    "Редактировать →": "Edit →",
    "заданы": "set",
    "отсутствуют": "missing",
    "неопасный": "non-hazardous",
    "активен": "active",
    "отключён": "disabled",
    "Порядок изменения справочников": "How reference data is changed",
    "Среда выполнения": "Runtime environment",
    "Параметры развёртывания": "Deployment settings",
    "Параметр": "Setting",
    "Значение": "Value",
    "Хранение": "Storage",
    "Заданий на выгрузку": "Export jobs",
    "Объём выгрузок": "Export volume",
    "Записей аудита": "Audit records",
    "Проверка доступности": "Availability check",
    "Выполнить проверку": "Run the check",
    "Штатная админка Django": "Built-in Django admin",
    "Все роли": "All roles",
    "Активные": "Active",
    "Заблокированные": "Blocked",
    "Применить": "Apply",
    "заблокирована": "blocked",
    "Заблокировать": "Block",
    "Разблокировать": "Unblock",
    "Пользователи не найдены.": "No users found.",
    "Сводки и аналитические расчёты кешируются. Сбросьте кеш после ручного изменения данных, чтобы страницы показали актуальные значения.":
        "Summaries and analytical calculations are cached. Clear the cache after manual data changes so pages show current values.",
    "Регламентная загрузка выполняется по расписанию на стороне сервера. Загрузка набора данных вручную выполняется командой:":
        "Scheduled loading runs on the server. A dataset can be loaded manually with the command:",
    "Конечная точка для системы мониторинга возвращает состояние соединения с базой и объёмы ключевых таблиц.":
        "The monitoring endpoint returns the database connection status and the size of the key tables.",

    # --- Аналитика ------------------------------------------------------------------------
    "3 месяца": "3 months",
    "6 месяцев": "6 months",
    "9 месяцев": "9 months",
    "12 месяцев": "12 months",
    "оценка методом наименьших квадратов": "least squares estimate",
    "доля объяснённой дисперсии": "share of explained variance",
    "Средняя ошибка (MAPE)": "Mean error (MAPE)",
    "фактические наблюдения": "actual observations",
    "прогноз": "forecast",
    "сценарный расчёт": "scenario modelling",
    "Индекс отражает положение округа": "The index reflects the district's position",
    "относительно других округов": "relative to other districts",
    "Изменение объёма перевозок, %%": "Change in transport volume, %%",
    "Изменение складских мощностей, %%": "Change in storage capacity, %%",
    "Изменение пропускной способности сети, %%": "Change in network throughput, %%",
    "баллов по всем округам": "points across all districts",
    "нагрузка растёт": "load rising",
    "нагрузка снижается": "load falling",
    "без существенных изменений": "no significant change",
    "Индекс = 0,30 · мощности + 0,30 · грузопоток + 0,25 · загруженность + 0,15 · аварийность":
        "Index = 0.30 · capacity + 0.30 · freight flow + 0.25 · congestion + 0.15 · incident rate",
    "Результат умножается на сто и округляется до одного знака. Индекс отражает":
        "The result is multiplied by one hundred and rounded to one decimal place. The index reflects",
    "относительное": "relative",
    "Шкала загруженности: 0 — свободное движение, 10 — движение остановлено.":
        "Congestion scale: 0 — free flow, 10 — traffic halted.",
    "По горизонтали — час суток, по вертикали — средний балл загруженности по всем участкам сети.":
        "The horizontal axis is the hour of day; the vertical axis is the average congestion score across all segments.",
    "— свободное движение и незначительные затруднения;":
        "— free flow and minor delays;",
    "— затруднённое движение, скорость ниже разрешённой;":
        "— heavy traffic, speed below the limit;",
    "— пробки, движение с частыми остановками;":
        "— congestion, frequent stops;",
    "— движение практически остановлено.":
        "— traffic almost at a standstill.",
    "каждый сегмент — участок сети, наведите для названия":
        "each segment is a network link; hover for its name",
    "Изменение условий перезагружает включённые слои.":
        "Changing the filters reloads the active layers.",

    # --- Прочие подписи -----------------------------------------------------------------------
    "Замечание к материалу?": "A remark about this article?",
    "ДОПОГ / ADR": "ADR",
    "классы опасности от 1 до 9": "hazard classes 1 to 9",
    "чел/км²": "people/km²",
    "Все объекты →": "All facilities →",
    "до 3 рабочих дней": "up to 3 working days",
    "администратор системы": "the system administrator",
    "последний месяц к первому": "last month against the first",
    "Прогноз →": "Forecast →",
    "Классификатор →": "Classifier →",
    "Не нашли ответа?": "Did not find an answer?",
    "Написать в поддержку": "Contact support",
    "Все участки сети": "All network segments",
    "свободно (0–4)": "free flow (0–4)",
    "затруднения (5–6)": "delays (5–6)",
    "пробки (7–8)": "congestion (7–8)",
    "движение остановлено (9–10)": "traffic halted (9–10)",
    "суммарно по всем объектам": "total across all facilities",
    "Подробнее →": "More →",
    "объём перевозок, т": "transport volume, t",
    "Все округа →": "All districts →",
    "Обстановка →": "Conditions →",
    "груз": "cargo",
    "Все материалы →": "All articles →",
    "открыт": "open",
    "устранён": "resolved",
    "круглосуточно": "round the clock",
    "км/ч": "km/h",
    "Сводка загруженности →": "Congestion summary →",
    "ТС/сут": "veh/day",
    "ТС·км": "veh·km",
    "суточная, по маршруту": "daily, per route",
    "Отправление → Назначение": "Origin → Destination",
    "Вход, регистрация и сведения о системе": "Sign-in, registration and system information",
    "Документация API": "API documentation",
    "ссылка": "link",
    "История обновлений →": "Update history →",
    "Записи": "Records",
    "требуют согласования маршрута и времени движения":
        "require route and timing approval",
    "Сообщите о неточности через форму обратной связи.":
        "Report an inaccuracy through the feedback form.",
    "Опишите вопрос подробно: если речь о конкретной записи, укажите её название или ссылку на страницу.":
        "Describe the issue in detail: if it concerns a specific record, give its name or a link to the page.",
    "PDF, изображение, таблица или документ до 10 МБ":
        "PDF, image, spreadsheet or document up to 10 MB",
    "Ответ будет направлен на указанный адрес электронной почты в течение трёх рабочих дней. Номер обращения указан в письме-подтверждении.":
        "A reply will be sent to the given email address within three working days. The enquiry number is stated in the confirmation letter.",
    "Задайте вопрос через форму обратной связи — обращения рассматривает администратор системы.":
        "Ask a question through the feedback form — enquiries are reviewed by the system administrator.",

    # --- Вход и регистрация ---------------------------------------------------------------------
    "Нет учётной записи?": "No account yet?",
    "Учётные записи создаются командой": "Accounts are created by the command",
    "и предназначены только для учебного контура.":
        "and are intended for the demonstration environment only.",
    "Пароль изменён": "Password changed",
    "В личный кабинет": "To the personal area",
    "Латинские буквы, цифры и символы @ . + - _": "Latin letters, digits and @ . + - _",
    "Уже зарегистрированы?": "Already registered?",
    "не менее восьми символов;": "at least eight characters;",
    "состоит не только из цифр.": "not made up of digits alone.",
    "не совпадает с именем пользователя и другими данными профиля;":
        "does not match the username or other profile details;",
    "не входит в перечень распространённых паролей;":
        "is not among commonly used passwords;",
    "Выгрузка отчётов и доступ к API — с роли «Аналитик»":
        "Report export and API access — from the Analyst role",
    "Расширенные роли назначает администратор системы по запросу через форму обратной связи.":
        "Extended roles are assigned by the system administrator on request through the feedback form.",
    "Неверное имя пользователя или пароль. Проверьте раскладку клавиатуры и регистр символов.":
        "Incorrect username or password. Check the keyboard layout and letter case.",
    "— роль «Наблюдатель» назначается автоматически.":
        "— the Viewer role is assigned automatically.",
    "Для ознакомления с возможностями ролей подготовлены учётные записи с паролем":
        "To explore the roles, demonstration accounts are provided with the password",
    "Новый пароль вступил в силу. Используйте его при следующем входе в систему.":
        "The new password is now in effect. Use it the next time you sign in.",
    # --- Роли и полномочия ------------------------------------------------------
    "Просмотр реестров, карты и аналитики, ведение избранного и сохранённых условий отбора.":
        "Viewing registers, the map and analytics; keeping favourites and saved filter settings.",
    "Дополнительно: выгрузка отчётов в форматах XLSX, DOCX, CSV и GeoJSON, работа с конструктором сравнений и доступ к REST API по токену.":
        "Additionally: exporting reports in XLSX, DOCX, CSV and GeoJSON, using the comparison builder and REST API access by token.",
    "Дополнительно: регистрация и закрытие дорожных инцидентов, редактирование карточек объектов и запуск процедур загрузки данных.":
        "Additionally: registering and closing traffic incidents, editing facility records and running data load procedures.",
    "Полный доступ: управление пользователями и ролями, ведение справочников, модерация обращений и просмотр журнала аудита.":
        "Full access: managing users and roles, maintaining reference data, moderating enquiries and viewing the audit log.",
    "Роли и права доступа": "Roles and permissions",
    "Просмотр всех публичных разделов, избранное, сохранённые виды.":
        "Viewing all public sections, favourites and saved views.",
    "Дополнительно — выгрузка отчётов и доступ к REST API по токену.":
        "Additionally — report export and REST API access by token.",
    "Дополнительно — регистрация инцидентов и правка карточек объектов.":
        "Additionally — registering incidents and editing facility records.",
    "Полный доступ, включая панель управления и журнал аудита.":
        "Full access, including the administration console and audit log.",

    # --- Сообщения системы -------------------------------------------------------
    "вся Москва": "all of Moscow",
    "Учётная запись создана. Роль «Наблюдатель» назначена по умолчанию; для расширения полномочий обратитесь к администратору.":
        "Account created. The Viewer role is assigned by default; contact the administrator for extended permissions.",
    "Сведения профиля обновлены.": "Profile details updated.",
    "Не удалось изменить закладку: некорректные параметры.":
        "Could not change the bookmark: invalid parameters.",
    "Закладка убрана.": "Bookmark removed.",
    "Добавлено в избранное.": "Added to favourites.",
    "Вид сохранён.": "View saved.",
    "Вид опубликован — доступ открыт по ссылке.": "View published — accessible by link.",
    "Публичный доступ к виду закрыт.": "Public access to the view revoked.",
    "Вид удалён.": "View deleted.",
    "Файл отчёта ещё не сформирован": "The report file has not been generated yet",
    "Файл отчёта удалён по истечении срока хранения":
        "The report file was removed after the retention period",
    "Подписка оформлена.": "Subscription created.",
    "Подписка удалена.": "Subscription removed.",
    "Все уведомления отмечены как прочитанные.": "All notifications marked as read.",
    "Доступ к программному интерфейсу предоставляется с роли «Аналитик».":
        "API access is available from the Analyst role.",
    "Новый токен выпущен. Предыдущий отозван.": "A new token has been issued. The previous one is revoked.",
    "Токен отозван.": "Token revoked.",
    "Нельзя изменить роль или заблокировать собственную запись.":
        "You cannot change the role of, or block, your own account.",
    "Текст ответа слишком короткий.": "The reply text is too short.",
    "Ответ сохранён, обращение переведено в состояние «Отвечено».":
        "The reply has been saved and the enquiry marked as answered.",
    "Обращение взято в работу.": "The enquiry has been taken into work.",
    "Обращение закрыто.": "The enquiry has been closed.",
    "Кеш сводок и аналитических расчётов сброшен.":
        "The cache of summaries and analytical calculations has been cleared.",
    "Раздел доступен администраторам системы": "This section is available to system administrators",
    "Обращение принято. Ответ будет направлен на указанный адрес электронной почты в течение трёх рабочих дней.":
        "Enquiry received. A reply will be sent to the given email address within three working days.",
    "Не указаны координаты точки": "Point coordinates were not provided",
    "Описание не предоставлено источником данных": "No description provided by the data source",
    "Адрес не указан в источнике данных": "No address provided by the data source",
    "Факт и прогноз объёма перевозок": "Actual and forecast transport volume",

    # --- Панель администратора: разделы --------------------------------------------
    "Состояние и ключевые счётчики": "Status and key counters",
    "Учётные записи и роли": "Accounts and roles",
    "Округа, типы, категории, источники": "Districts, types, categories, sources",
    "Обратная связь и ответы": "Feedback and replies",
    "Аналитические публикации": "Analytical publications",
    "Полнота и целостность записей": "Record completeness and integrity",
    "Журнал и запуск процедур": "Log and procedure runs",
    "Действия всех пользователей": "Activity of all users",

    # --- Проверки качества данных -------------------------------------------------------
    "Объекты без координат": "Facilities without coordinates",
    "Не отображаются на карте и не участвуют в поиске «что рядом»":
        "Not shown on the map and excluded from proximity search",
    "Объекты без указания мощности": "Facilities without stated capacity",
    "Не учитываются в расчёте обеспеченности округа":
        "Excluded from the district provision calculation",
    "Объекты без адреса": "Facilities without an address",
    "Затрудняет идентификацию объекта пользователем":
        "Makes it harder for users to identify the facility",
    "Участки сети без геометрии": "Network segments without geometry",
    "Не отображаются на слое дорожной сети": "Not shown on the road network layer",
    "Инциденты без привязки к участку": "Incidents not linked to a segment",
    "Не попадают в статистику аварийности по округам":
        "Excluded from district incident statistics",
    "Незакрытые инциденты старше 30 суток": "Open incidents older than 30 days",
    "Вероятно, отсутствует отметка об устранении": "The resolution mark is probably missing",
    "Записи без указания источника": "Records without a stated source",
    "Невозможно проследить происхождение сведений": "The origin of the data cannot be traced",

    # --- Состояние среды ------------------------------------------------------------------
    "недоступно": "unavailable",
    "Версия Django": "Django version",
    "Версия Python": "Python version",
    "Операционная система": "Operating system",
    "Система управления БД": "Database management system",
    "Объём базы данных": "Database size",
    "Режим отладки": "Debug mode",
    "включён": "enabled",
    "выключен": "disabled",
    "Часовой пояс": "Time zone",
    "Язык по умолчанию": "Default language",
    "Каталог выгрузок": "Export directory",
    "Срок хранения выгрузок": "Export retention period",

    # --- Программный интерфейс: описания методов ------------------------------------------------
    "Справочник округов с площадью, населением и координатами центра.":
        "District reference data with area, population and centre coordinates.",
    "Реестр складов, терминалов и распределительных центров.":
        "Register of warehouses, terminals and distribution centres.",
    "Объекты рядом с точкой": "Facilities near a point",
    "Поиск ближайших объектов по координатам и радиусу.":
        "Search for the nearest facilities by coordinates and radius.",
    "Магистрали под мониторингом с текущей загруженностью.":
        "Monitored arterials with current congestion.",
    "Текущая обстановка": "Current conditions",
    "Последний замер загруженности по каждому участку сети.":
        "The latest congestion reading for each network segment.",
    "Журнал происшествий, работ и ограничений движения.":
        "Log of accidents, roadworks and traffic restrictions.",
    "Транспортные коридоры ввоза, вывоза и транзита.":
        "Transport corridors for inbound, outbound and transit traffic.",
    "Помесячные объёмы перевозок по округам и категориям грузов.":
        "Monthly transport volumes by district and cargo category.",
    "Композитная оценка нагрузки на инфраструктуру округов.":
        "Composite assessment of district infrastructure load.",
    "Оценка объёма перевозок на ближайшие месяцы.":
        "Transport volume projection for the coming months.",

    # --- Справка -------------------------------------------------------------------------------------
    "С чего начать": "Getting started",
    "Главная страница": "Home page",
    "Оперативная сводка: число объектов, суммарная мощность хранения, средняя загруженность сети и открытые инциденты. Каждый показатель ведёт в соответствующий раздел.":
        "Operational summary: number of facilities, total storage capacity, average network congestion and open incidents. Each indicator links to the corresponding section.",
    "Пространственный обзор. Слои включаются независимо: объекты, дорожная сеть с раскраской по загруженности, маршруты, инциденты. Инструмент «что рядом» показывает ближайшие объекты к указанной точке.":
        "Spatial overview. Layers are toggled independently: facilities, the road network coloured by congestion, routes and incidents. The proximity tool shows the facilities nearest to a chosen point.",
    "Реестры": "Registers",
    "Табличное представление с отбором и сортировкой. Любое состояние реестра адресуемо: ссылку можно сохранить или передать коллеге.":
        "A tabular view with filtering and sorting. Every state of a register is addressable: the link can be saved or shared with a colleague.",
    "Отбор и сортировка": "Filtering and sorting",
    "Наложение условий": "Applying filters",
    "Условия отбора комбинируются: округ, тип объекта, источник данных, поисковый запрос. Сброс выполняется кнопкой «Очистить».":
        "Filters combine: district, facility type, data source and search query. Use the Clear button to reset them.",
    "Сортировка": "Sorting",
    "Щелчок по заголовку колонки меняет порядок сортировки. Текущий порядок сохраняется при переходе между страницами списка.":
        "Clicking a column heading changes the sort order. The current order is preserved when moving between pages.",
    "Авторизованный пользователь может сохранить настроенный отбор в личном кабинете и открыть его позднее одним щелчком.":
        "A signed-in user can save the configured filters in their personal area and reopen them later with a single click.",
    "Аналитические разделы": "Analytical sections",
    "Композитная оценка округов по четырём составляющим: обеспеченность мощностями, интенсивность грузопотока, загруженность сети и аварийность. Методика приведена в разделе «Методология».":
        "A composite district score from four components: capacity provision, freight flow intensity, network congestion and incident rate. The method is described in the Methodology section.",
    "Разбиение округов на однородные группы методом k-средних по нормированным показателям.":
        "Grouping districts into homogeneous clusters by k-means on normalised indicators.",
    "Оценка объёма перевозок на ближайшие месяцы по модели тренда с сезонной составляющей.":
        "Transport volume projection for the coming months using a trend model with a seasonal component.",
    "Моделирование «что если»: изменение объёма перевозок или складских мощностей и оценка последствий для нагрузки на сеть.":
        "What-if modelling: changing transport volume or storage capacity and assessing the effect on network load.",
    "Избранное и сохранённые виды": "Favourites and saved views",
    "Закладки на объекты, округа и участки; сохранённые условия отбора с возможностью публикации по ссылке.":
        "Bookmarks for facilities, districts and segments; saved filter settings that can be shared by link.",
    "Формирование отчётов в форматах XLSX, DOCX, CSV, PDF и GeoJSON. Доступно с роли «Аналитик».":
        "Report generation in XLSX, DOCX, CSV, PDF and GeoJSON. Available from the Analyst role.",
    "Подписки и уведомления": "Subscriptions and notifications",
    "Оповещение о дорожных событиях выбранного округа с заданным порогом серьёзности.":
        "Notification of traffic incidents in a chosen district above a given severity threshold.",
    "Выпуск и отзыв персонального токена для программного доступа.":
        "Issuing and revoking a personal token for programmatic access.",
    "Дополнительно: регистрация и закрытие дорожных инцидентов, редактирование карточек объектов инфраструктуры, запуск загрузки данных.":
        "Additionally: registering and closing traffic incidents, editing infrastructure facility records and running data loads.",
    "Полный доступ: управление пользователями и ролями, ведение справочников, модерация обращений, журнал аудита, настройки системы.":
        "Full access: managing users and roles, maintaining reference data, moderating enquiries, the audit log and system settings.",
    "Учётная запись создана. Роль «Наблюдатель» назначена по умолчанию; для расширения прав обратитесь к администратору системы.":
        "Account created. The Viewer role is assigned by default; contact the system administrator for extended permissions.",
    # --- Строки с подстановкой ---------------------------------------------------
    "подписок: %(count)s": "subscriptions: %(count)s",
    "активных %(active)s, за неделю входили %(recent)s":
        "%(active)s active, %(recent)s signed in this week",
    "всего в системе %(total)s": "%(total)s in total",
    "запусков %(runs)s, сбоев %(failed)s": "%(runs)s runs, %(failed)s failures",
    "страница %(page)s из %(total)s": "page %(page)s of %(total)s",
    "требует внимания": "needs attention",
    "замечание": "remark",
    "информация": "information",
    "Длина, км": "Length, km",
    "Полос": "Lanes",
    "Время, ч": "Time, h",
    # --- Типология и оценка качества прогноза ------------------------------------
    "Периферийные округа с низкой нагрузкой": "Peripheral districts with low load",
    "Округа сбалансированного профиля": "Districts with a balanced profile",
    "Округа концентрации складских мощностей": "Districts concentrating storage capacity",
    "Округа предельной транспортной нагрузки": "Districts under peak transport load",
    "Недостаточно наблюдений": "Not enough observations",
    "не определено": "undetermined",
    "высокое": "high",
    "приемлемое": "acceptable",
    "низкое — прогноз носит ориентировочный характер":
        "low — the forecast is indicative only",
    "Нет данных": "No data",

    # --- Классы опасности по ДОПОГ -------------------------------------------------
    "Неопасный груз": "Non-hazardous cargo",
    "Класс 1 — взрывчатые вещества": "Class 1 — explosives",
    "Класс 2 — газы": "Class 2 — gases",
    "Класс 3 — легковоспламеняющиеся жидкости": "Class 3 — flammable liquids",
    "Класс 4 — легковоспламеняющиеся твёрдые вещества": "Class 4 — flammable solids",
    "Класс 5 — окисляющие вещества": "Class 5 — oxidising substances",
    "Класс 6 — токсичные и инфекционные вещества": "Class 6 — toxic and infectious substances",
    "Класс 7 — радиоактивные материалы": "Class 7 — radioactive material",
    "Класс 8 — коррозионные вещества": "Class 8 — corrosive substances",
    "Класс 9 — прочие опасные вещества": "Class 9 — miscellaneous dangerous substances",
    "Выполнен вход в систему": "Signed in",
    "Выполнен выход из системы": "Signed out",
    "из %(total)s · %(share)s %%": "of %(total)s · %(share)s %%",
    "%(km)s км под наблюдением": "%(km)s km monitored",
    "Оперативная сводка": "Operational summary",
    "%(count)s влияют на грузовой транспорт": "%(count)s affect freight traffic",
    "%(value)s из 10": "%(value)s out of 10",
}
