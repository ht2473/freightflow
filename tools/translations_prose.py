"""Перевод пояснительных разделов интерфейса.

Отдельный файл для связных текстов: описания методики, назначения системы и
порядка работы с разделами. Они переведены как связный текст, а не построчно,
поэтому вынесены из общего словаря коротких подписей.
"""

from __future__ import annotations

PROSE: dict[str, str] = {
    "Программный доступ по токену предоставляется с роли «Аналитик». Справочники и реестры при этом доступны без авторизации — токен лишь повышает допустимую частоту обращений.":
        "Token-based programmatic access is available from the Analyst role. Reference data and registers remain open without authorisation — the token only raises the permitted request rate.",

    "Информационная система мониторинга и анализа логистической инфраструктуры города Москвы: складские мощности, грузовые маршруты и обстановка на улично-дорожной сети.":
        "An information system for monitoring and analysing the logistics infrastructure of Moscow: storage capacity, freight routes and road network conditions.",

    "Перечень проверок составлен по принципу «дефект, искажающий выводы». В него включены только те условия, наличие которых меняет результат агрегации или делает запись недоступной пользователю: объект без координат не попадает на карту, объект без мощности не участвует в расчёте обеспеченности округа, инцидент без привязки к участку — в статистике аварийности.":
        "The list of checks follows the principle of “a defect that distorts conclusions”. It includes only conditions that change an aggregate or make a record unavailable to users: a facility without coordinates does not appear on the map, a facility without stated capacity is excluded from district provision figures, and an incident not linked to a segment is excluded from incident statistics.",

    "Ненулевое число замечаний не свидетельствует о неисправности системы. Пропуски отражают состояние исходных источников: часть сведений поступает в неполном виде и уточняется при последующих загрузках. Устранение выполняется на стороне поставщика данных — через запрос на уточнение, — либо ручным дополнением в административной панели.":
        "A non-zero number of findings does not indicate a fault in the system. Gaps reflect the state of the upstream sources: some data arrives incomplete and is refined in later loads. Correction is made either by the data provider, through a clarification request, or manually in the administration panel.",

    "Справочники редактируются через штатную административную панель Django с контролем ссылочной целостности: удаление записи, на которую есть ссылки, блокируется. Изменение наименования отражается во всех разделах системы немедленно, изменение кода — только после повторной загрузки данных, так как код используется при сопоставлении записей из внешних источников.":
        "Reference data is edited through the built-in Django administration panel with referential integrity enforced: deleting a record that is referenced elsewhere is blocked. A change of name takes effect across the system immediately; a change of code takes effect only after the data is reloaded, because the code is used to match records from external sources.",

    "«ГрузПоток» — информационно-аналитическая система, объединяющая сведения о логистической инфраструктуре Москвы в единой среде: складские мощности и терминалы, магистральную сеть с оценкой загруженности, грузовые коридоры и статистику перевозок.":
        "FreightFlow is an analytical information system that brings together data on Moscow logistics infrastructure in a single environment: storage capacity and terminals, the arterial network with congestion assessment, freight corridors and transport statistics.",

    "Практическая задача системы — сократить время, необходимое для получения ответа на вопросы вида «где сосредоточены мощности хранения», «какие направления перегружены в утренний пик» и «как изменится нагрузка на округ при росте объёма перевозок». До консолидации данных такие вопросы требовали обращения к нескольким ведомственным источникам и ручного сопоставления разнородных выгрузок.":
        "The practical purpose of the system is to shorten the time needed to answer questions such as “where is storage capacity concentrated”, “which directions are overloaded during the morning peak” and “how will the load on a district change if transport volume grows”. Before the data was consolidated, such questions required consulting several departmental sources and manually reconciling dissimilar extracts.",

    "Система построена на платформе Django и следует классической многослойной организации: слой моделей отвечает за представление предметной области, слой выборок — за агрегацию, представления — за подготовку контекста страниц. Аналитические расчёты вынесены в отдельный модуль и не зависят от способа отображения результата.":
        "The system is built on Django and follows a classic layered structure: the model layer represents the subject domain, the selector layer handles aggregation and the views prepare page context. Analytical calculations are placed in a separate module and do not depend on how the result is displayed.",

    "Пространственный слой реализован без внешних библиотек геообработки. Хранение и запросы выполняет сама СУБД: в промышленном контуре это PostgreSQL с расширением PostGIS, в контуре разработки и в автотестах — SQLite с текстовым представлением геометрии. Прикладной код одинаков для обоих вариантов, что позволяет разворачивать систему без установки системных зависимостей.":
        "The spatial layer is implemented without external geoprocessing libraries. Storage and queries are handled by the database itself: PostgreSQL with the PostGIS extension in production, and SQLite with a textual geometry representation in development and automated tests. The application code is identical for both, which allows the system to be deployed without installing system dependencies.",

    "Предусмотрены четыре роли с вложенной моделью полномочий. «Наблюдатель» работает с реестрами и картой, «Аналитик» дополнительно получает выгрузку отчётов и программный доступ, «Диспетчер» вносит сведения о дорожных событиях, «Администратор» управляет учётными записями, справочниками и настройками. Все значимые действия фиксируются в журнале аудита.":
        "There are four roles with nested permissions. The Viewer works with registers and the map; the Analyst additionally gets report export and programmatic access; the Operator records traffic incidents; the Administrator manages accounts, reference data and settings. All significant actions are recorded in the audit log.",

    "Реестры и аналитические показатели доступны без авторизации как через веб-интерфейс, так и через программный интерфейс REST со спецификацией OpenAPI. Полнота исходных данных публикуется открыто: показатели заполненности таблиц приведены в разделе источников, а автоматические проверки качества доступны администратору.":
        "Registers and analytical indicators are available without authorisation, both through the web interface and through the REST API with an OpenAPI specification. Source data completeness is published openly: table population figures are given in the sources section, and automatic quality checks are available to the administrator.",

    "Модель складывает линейный тренд и сезонную составляющую, оценённую как среднее отклонение соответствующего месяца от линии тренда. Простая форма модели выбрана осознанно: на коротком ряде наблюдений сложные модели дают неустойчивые оценки параметров и кажущуюся точность.":
        "The model adds a linear trend and a seasonal component estimated as the average deviation of the corresponding month from the trend line. The simple form is a deliberate choice: on a short series, complex models produce unstable parameter estimates and only apparent accuracy.",

    "Средняя абсолютная процентная ошибка ниже 10 %% считается высоким качеством, от 10 до 20 %% — приемлемым, свыше 20 %% — низким: в последнем случае прогноз следует трактовать как ориентировочный.":
        "A mean absolute percentage error below 10 %% is considered high quality, from 10 to 20 %% acceptable, and above 20 %% low: in the latter case the forecast should be treated as indicative only.",

    "Прогноз описывает продолжение сложившихся тенденций и не учитывает изменения условий — ввод новых мощностей, изменение режима допуска транспорта, крупные ремонтные работы. Для оценки таких изменений предназначен":
        "The forecast describes a continuation of established trends and does not account for changing conditions — new capacity coming online, changes to vehicle access rules or major roadworks. To assess such changes, use",

    "Четыре показателя округа нормируются методом «минимум — максимум», умножаются на веса и суммируются. Результат приводится к стобалльной шкале.":
        "Four district indicators are normalised by the min-max method, multiplied by their weights and summed. The result is expressed on a hundred-point scale.",

    ", а не абсолютный уровень нагрузки. Округ с индексом 100 имеет наибольшие значения показателей в текущей выборке, а не «предельно возможные».":
        ", rather than an absolute level of load. A district with an index of 100 has the highest indicator values in the current sample, not the highest possible ones.",

    "Высокий индекс не означает проблемы сам по себе: он может отражать концентрацию складских мощностей — то есть развитость инфраструктуры. Смысл конкретного значения раскрывают составляющие: сочетание высокой загруженности при низких мощностях указывает на транзитную перегрузку, обратное сочетание — на логистический узел с достаточной пропускной способностью.":
        "A high index does not in itself indicate a problem: it may reflect a concentration of storage capacity, that is, well-developed infrastructure. The components reveal the meaning of a particular value: high congestion combined with low capacity points to transit overload, while the reverse combination indicates a logistics hub with sufficient throughput.",

    "Загруженность сети реагирует на изменение условий эластично: рост грузопотока увеличивает её с коэффициентом 0,6, увеличение пропускной способности снижает с коэффициентом 0,4. Складские мощности и объём перевозок входят в расчёт напрямую.":
        "Network congestion responds elastically to changing conditions: growth in freight flow raises it with a coefficient of 0.6, while greater throughput lowers it with a coefficient of 0.4. Storage capacity and transport volume enter the calculation directly.",

    "После пересчёта показателей выполняется повторное нормирование, поэтому индексы остаются сопоставимыми между округами. Обратите внимание: при равномерном изменении условий во всех округах относительные позиции сохраняются, а меняются лишь абсолютные значения составляющих.":
        "After the indicators are recalculated, normalisation is applied again, so the indices remain comparable across districts. Note that if conditions change uniformly in all districts, relative positions are preserved and only the absolute values of the components change.",

    "Коэффициенты эластичности заданы экспертно и подлежат уточнению по мере накопления наблюдений. Результат расчёта следует трактовать как оценку направления и порядка изменений, а не как точный прогноз.":
        "The elasticity coefficients are set by expert judgement and are subject to refinement as observations accumulate. The result should be read as an estimate of the direction and order of magnitude of change, not as a precise forecast.",

    "Начальные центры выбираются по схеме k-means++, снижающей риск попадания в неудачный локальный минимум. Генератор случайных чисел зафиксирован, поэтому при одних и тех же данных результат воспроизводится в точности — свойство, необходимое для проверки расчётов.":
        "Initial centres are chosen using the k-means++ scheme, which reduces the risk of settling in a poor local minimum. The random number generator is seeded, so the same data always yields exactly the same result — a property required for verifying the calculations.",

    "Названия групп присвоены по возрастанию средней нагрузки и носят описательный характер: содержательная интерпретация требует рассмотрения состава каждой группы.":
        "Cluster names are assigned in ascending order of average load and are descriptive only: a substantive interpretation requires examining the membership of each cluster.",

    "Справочники и реестры доступны без авторизации. Персональный токен требуется только для повышенных ограничений на частоту обращений; он выпускается в личном кабинете пользователям с ролью «Аналитик» и выше.":
        "Reference data and registers are available without authorisation. A personal token is required only for higher request rate limits; it is issued in the personal area to users with the Analyst role and above.",

    "Пространственные данные передаются в формате GeoJSON в системе координат WGS-84, порядок координат — долгота, широта. Ответ можно передать в картографическую библиотеку без промежуточных преобразований.":
        "Spatial data is delivered as GeoJSON in the WGS-84 coordinate system, with coordinates ordered longitude then latitude. The response can be passed to a mapping library without intermediate conversion.",

    "Класс опасности определяется Европейским соглашением о международной дорожной перевозке опасных грузов (ДОПОГ). Перевозка грузов с ненулевым классом требует специального разрешения, согласования маршрута и временных интервалов движения, а также применения транспортных средств соответствующего исполнения. Эти ограничения напрямую влияют на выбор коридоров: часть направлений для таких перевозок недоступна.":
        "The hazard class is defined by the European Agreement concerning the International Carriage of Dangerous Goods by Road (ADR). Carrying cargo with a non-zero class requires a special permit, approval of the route and travel time windows, and vehicles of the appropriate design. These restrictions directly affect corridor selection: some directions are unavailable for such transport.",

    "Замечания к данным передаются поставщику источника, предложения по развитию системы включаются в план доработок. Ответ направляется на указанный адрес электронной почты.":
        "Data remarks are forwarded to the source provider, and improvement suggestions are added to the development plan. A reply is sent to the email address given.",

    "Замеры за шесть часов до регистрации события и шесть часов после его устранения — позволяют оценить фактическое влияние на движение.":
        "Readings from six hours before the incident was reported to six hours after it was resolved — these allow the actual effect on traffic to be assessed.",

    "Система консолидирует сведения из восьми источников, различающихся по природе, полноте и регламенту обновления. Атрибутивная часть поступает из ведомственных информационных систем и с портала открытых данных, пространственная — из геоинформационных сервисов, телеметрия дорожной обстановки — из системы мониторинга движения.":
        "The system consolidates data from eight sources that differ in nature, completeness and update schedule. Attribute data comes from departmental information systems and the open data portal, spatial data from geographic information services, and road condition telemetry from the traffic monitoring system.",

    "Все поступающие записи проходят приведение к единой схеме хранения: координаты переводятся в систему WGS-84, отметки времени — в московский часовой пояс, единицы измерения — в тонны, километры и километры в час. Записи, не удовлетворяющие ограничениям целостности, отклоняются, а их число фиксируется в журнале загрузок.":
        "All incoming records are harmonised to a single storage schema: coordinates are converted to WGS-84, timestamps to Moscow time, and units to tonnes, kilometres and kilometres per hour. Records that fail integrity constraints are rejected, and their number is recorded in the load log.",

    "Загруженность участка выражается целым баллом от нуля до десяти по шкале, принятой в практике городского мониторинга движения. Нулевое значение соответствует свободному движению с разрешённой скоростью, десятое — полной остановке потока. Для группировки в интерфейсе применяются четыре интервала.":
        "Segment congestion is expressed as an integer score from zero to ten on the scale used in urban traffic monitoring practice. Zero corresponds to free flow at the speed limit, ten to a complete standstill. Four bands are used for grouping in the interface.",

    "Средняя загруженность сети рассчитывается по последнему замеру каждого участка, а не по всем записям за период. Это принципиально: участки с более частой телеметрией иначе получили бы непропорционально больший вес в итоговой оценке.":
        "Average network congestion is calculated from the latest reading for each segment rather than from all readings in the period. This matters: otherwise segments with more frequent telemetry would carry disproportionate weight in the result.",

    "Показатели округа измеряются в разных единицах, поэтому прямое их сложение лишено смысла. Применяется нормирование методом «минимум — максимум»: каждый показатель линейно отображается на отрезок от нуля до единицы, где ноль соответствует наименьшему значению в выборке, единица — наибольшему.":
        "District indicators are measured in different units, so adding them directly would be meaningless. Min-max normalisation is applied: each indicator is mapped linearly onto the interval from zero to one, where zero is the smallest value in the sample and one the largest.",

    "положение округа: при изменении состава выборки значения пересчитываются. Поэтому он пригоден для ранжирования и выявления полярных случаев, но не является абсолютной мерой нагрузки.":
        "position of a district: values are recalculated whenever the sample changes. It is therefore suitable for ranking and for identifying extremes, but it is not an absolute measure of load.",

    "Разбиение на однородные группы выполняется методом k-средних в пространстве четырёх стандартизованных показателей. Стандартизация обязательна: без неё расстояние между округами определялось бы почти исключительно объёмом грузопотока, измеряемым десятками тысяч тонн.":
        "Grouping into homogeneous clusters is performed by k-means in the space of four standardised indicators. Standardisation is essential: without it, the distance between districts would be determined almost entirely by freight volume, measured in tens of thousands of tonnes.",

    "Начальные центры выбираются по схеме k-means++, снижающей вероятность попадания в неудачный локальный минимум. Генератор случайных чисел зафиксирован, поэтому результат воспроизводим при повторных запусках — свойство, существенное для проверяемости расчётов.":
        "Initial centres are chosen using the k-means++ scheme, which reduces the chance of settling in a poor local minimum. The random number generator is seeded, so the result is reproducible across runs — a property essential for verifiability.",

    "Применяется аддитивная модель: линейный тренд, оценённый методом наименьших квадратов, плюс сезонная составляющая, рассчитанная как среднее отклонение соответствующего месяца от линии тренда.":
        "An additive model is used: a linear trend estimated by least squares plus a seasonal component calculated as the average deviation of the corresponding month from the trend line.",

    "Выбор в пользу простой модели обусловлен длиной ряда наблюдений. На коротких рядах методы класса SARIMA дают неустойчивые оценки параметров, и кажущаяся точность модели не соответствует её фактической предсказательной способности. Качество аппроксимации характеризуется коэффициентом детерминации и средней абсолютной процентной ошибкой; оба показателя выводятся на странице прогноза.":
        "The choice of a simple model is driven by the length of the observation series. On short series, SARIMA-class methods give unstable parameter estimates, and the apparent accuracy of the model does not match its actual predictive power. Fit quality is characterised by the coefficient of determination and the mean absolute percentage error; both are shown on the forecast page.",

    "Отклик загруженности сети на изменение условий описывается эластичной зависимостью: рост грузопотока увеличивает загруженность с коэффициентом 0,6, увеличение пропускной способности сети снижает её с коэффициентом 0,4. Значения коэффициентов заданы экспертно и подлежат уточнению по мере накопления наблюдений.":
        "The response of network congestion to changing conditions is described by an elastic relationship: growth in freight flow raises congestion with a coefficient of 0.6, while greater network throughput lowers it with a coefficient of 0.4. The coefficients are set by expert judgement and are subject to refinement as observations accumulate.",

    "Результаты расчётов носят информационно-аналитический характер. Полнота исходных данных влияет на достоверность агрегатов: объект без указания мощности не участвует в расчёте обеспеченности округа, инцидент без привязки к участку — в статистике аварийности. Текущие показатели полноты приведены ниже и в разделе источников данных.":
        "The results are informational and analytical in nature. Source data completeness affects the reliability of aggregates: a facility without stated capacity is excluded from district provision figures, and an incident not linked to a segment is excluded from incident statistics. Current completeness figures are given below and in the data sources section.",
}
