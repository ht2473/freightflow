/* ==========================================================================
   ИС «ГрузПоток» — интерактивная карта

   Карта строится на MapLibre GL и на собственных векторных тайлах: и
   библиотека, и данные обслуживаются с домена системы, поэтому внешние
   службы при работе не требуются.

   Слои приходят одним источником — тайлом того квадрата сетки, который
   виден на экране. Поэтому включение слоя, отбор по округу и смена
   показателя раскраски выполняются на уже полученных данных, без
   обращения к серверу: перерисовка мгновенна, а объём переданного
   зависит от видимой области, а не от размера реестра.
   ========================================================================== */

(function () {
    "use strict";

    var node = document.getElementById("map-canvas");
    if (!node || typeof maplibregl === "undefined" || !window.ffBasemap) return;

    var ffBasemap = window.ffBasemap;

    /* Настройки приходят отдельным элементом application/json, собранным на
       стороне сервера: подстановка чисел прямо в разметку проходит через
       локализацию и при русском языке даёт десятичную запятую. */
    var settingsNode = document.getElementById("map-settings");
    if (!settingsNode) return;

    var settings;
    try {
        settings = JSON.parse(settingsNode.textContent);
    } catch (error) {
        return;
    }

    var SOURCE = "freightflow";

    /* ----------------------------------------------------------------------
       Цвета оформления
       ---------------------------------------------------------------------- */

    var palette = ffBasemap.palette();

    function toneMatch(colors) {
        return [
            "match",
            ["get", "tone"],
            "ok", colors.ok,
            "warn", colors.warn,
            "alert", colors.alert,
            "crit", colors.crit,
            colors.muted
        ];
    }

    /* ----------------------------------------------------------------------
       Раскраска округов по выбранному показателю
       ---------------------------------------------------------------------- */

    var metrics = settings.choropleth || [];
    var currentMetric = metrics.length ? metrics[0] : null;

    /** Заливка округа: от светлого к насыщенному по величине показателя. */
    function districtFill(colors) {
        if (!currentMetric || !currentMetric.max) return colors.muted;
        return [
            "case",
            ["has", currentMetric.property],
            [
                "interpolate",
                ["linear"],
                ["to-number", ["get", currentMetric.property]],
                0, colors.land,
                currentMetric.max / 2, colors.warn,
                currentMetric.max, colors.alert
            ],
            // Округ, по которому показатель не измерен, остаётся нейтральным:
            // ноль и «нет сведений» — разные состояния.
            colors.line
        ];
    }

    /* ----------------------------------------------------------------------
       Состав слоёв карты
       ---------------------------------------------------------------------- */

    function buildStyle(colors) {
        /* Подложка — вода, зелёные массивы, магистральная сеть и границы
           округов — общая с картами карточек: определение одно, и карта
           склада выглядит так же, как обзорная карта города. Здесь к ней
           добавляются слои, которые существуют только в разделе. */
        var style = ffBasemap.style(colors, {
            tilejson: settings.urls.tilejson,
            attribution: settings.attribution,
            roadColor: toneMatch(colors)
        });

        style.sources.probe = {
            type: "geojson",
            data: { type: "FeatureCollection", features: [] }
        };
        style.sources.isochrones = {
            type: "geojson",
            data: { type: "FeatureCollection", features: [] }
        };
        style.sources.route = {
            type: "geojson",
            data: { type: "FeatureCollection", features: [] }
        };

        // Раскраска округов ложится на самый низ: вода и зелень читаются
        // поверх неё, иначе показатель закрашивает рисунок города.
        ffBasemap.insertBefore(style.layers, "green", {
            id: "districts-fill",
            type: "fill",
            source: SOURCE,
            "source-layer": "districts",
            paint: { "fill-color": districtFill(colors), "fill-opacity": 0.42 }
        });

        // Каркас — подложка под самими магистралями: по этим улицам
        // движение грузового транспорта разрешено, вне их действует порог
        // разрешённой максимальной массы 2,5 т.
        ffBasemap.insertBefore(style.layers, "roads", {
            id: "freight-frame",
            type: "line",
            source: SOURCE,
            "source-layer": "roads",
            filter: ["==", ["get", "freight_frame"], true],
            layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
            paint: {
                "line-color": colors.ok,
                "line-width": ["interpolate", ["linear"], ["zoom"], 7, 4, 12, 8, 16, 16],
                "line-opacity": 0.45
            }
        });

        style.layers = style.layers.concat([
            {
                id: "zones-outline",
                type: "line",
                source: SOURCE,
                "source-layer": "zones",
                layout: { visibility: "none" },
                paint: {
                    "line-color": colors.crit,
                    "line-width": 2,
                    "line-dasharray": [3, 2]
                }
            },
            {
                id: "footprints",
                type: "fill",
                source: SOURCE,
                "source-layer": "footprints",
                minzoom: 14,
                paint: {
                    "fill-color": colors.accent,
                    "fill-opacity": 0.35,
                    "fill-outline-color": colors.accent
                }
            },
            {
                id: "routes",
                type: "line",
                source: SOURCE,
                "source-layer": "routes",
                layout: { visibility: "none", "line-cap": "round" },
                paint: {
                    "line-color": colors.route,
                    "line-width": ["interpolate", ["linear"], ["zoom"], 7, 1.5, 14, 5],
                    "line-dasharray": [2, 1]
                }
            },
            {
                id: "isochrones",
                type: "fill",
                source: "isochrones",
                paint: {
                    // Чем меньше время хода, тем насыщеннее заливка:
                    // ближняя зона доступности читается поверх дальней.
                    "fill-color": colors.accent,
                    "fill-opacity": [
                        "interpolate", ["linear"], ["to-number", ["get", "minutes"]],
                        5, 0.35, 30, 0.1
                    ],
                    "fill-outline-color": colors.accent
                }
            },
            {
                id: "objects",
                type: "circle",
                source: SOURCE,
                "source-layer": "objects",
                paint: {
                    "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 13, 5, 16, 8],
                    "circle-color": colors.accent,
                    "circle-opacity": 0.9,
                    "circle-stroke-width": [
                        "interpolate", ["linear"], ["zoom"], 9, 0.4, 13, 1
                    ],
                    "circle-stroke-color": colors.land
                }
            },
            {
                id: "incidents",
                type: "circle",
                source: SOURCE,
                "source-layer": "incidents",
                layout: { visibility: "none" },
                paint: {
                    "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 16, 9],
                    "circle-color": toneMatch(colors),
                    "circle-stroke-width": 1,
                    "circle-stroke-color": colors.land
                }
            },
            {
                id: "route-line",
                type: "line",
                source: "route",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": colors.ink,
                    "line-width": ["interpolate", ["linear"], ["zoom"], 9, 3, 16, 8],
                    "line-opacity": 0.85
                }
            },
            {
                id: "probe-results",
                type: "circle",
                source: "probe",
                paint: {
                    "circle-radius": 7,
                    "circle-color": "rgba(0,0,0,0)",
                    "circle-stroke-width": 2,
                    "circle-stroke-color": colors.ink
                }
            }
        ]);

        return style;
    }

    /* ----------------------------------------------------------------------
       Создание карты
       ---------------------------------------------------------------------- */

    var map;
    try {
        map = new maplibregl.Map({
            container: node,
            style: buildStyle(palette),
            center: [settings.center[1], settings.center[0]],
            zoom: settings.zoom,
            minZoom: settings.minZoom,
            maxZoom: settings.maxZoom,
            maxBounds: settings.maxBounds,
            attributionControl: false
        });
    } catch (error) {
        node.innerHTML =
            "<p class='small faint' style='padding:var(--sp-4)'>" +
            "Карта требует поддержки WebGL. Разделы «Реестр объектов» и " +
            "«Дорожная обстановка» показывают те же данные таблицами.</p>";
        return;
    }

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }));
    map.addControl(
        new maplibregl.AttributionControl({ compact: true, customAttribution: settings.attribution })
    );

    /* ----------------------------------------------------------------------
       Подписи округов
       ---------------------------------------------------------------------- */

    /* Названия выводятся разметкой, а не средствами тайлового стиля: подписи
       в стиле рисуются растеризованными наборами глифов, которые пришлось бы
       обслуживать отдельной службой ради двенадцати названий. */
    var districtLabels = (settings.districts || []).map(function (item) {
        var element = document.createElement("span");
        element.className = "map-label";
        element.textContent = item.short_name;
        element.title = item.name;
        return new maplibregl.Marker({ element: element, anchor: "center" })
            .setLngLat([item.lon, item.lat])
            .addTo(map);
    });

    function toggleLabels(visible) {
        districtLabels.forEach(function (marker) {
            marker.getElement().style.display = visible ? "" : "none";
        });
    }

    /* ----------------------------------------------------------------------
       Всплывающие карточки
       ---------------------------------------------------------------------- */

    function escapeHtml(text) {
        var box = document.createElement("div");
        box.textContent = text === null || text === undefined ? "" : String(text);
        return box.innerHTML;
    }

    function number(value, digits) {
        if (value === null || value === undefined || value === "") return null;
        return Number(value).toLocaleString("ru-RU", {
            maximumFractionDigits: digits === undefined ? 0 : digits
        });
    }

    function row(label, value) {
        if (value === null || value === undefined || value === "") return "";
        return "<div class='map-popup__row'><span>" + escapeHtml(label) + "</span>"
            + "<span>" + escapeHtml(value) + "</span></div>";
    }

    function card(title, subtitle, rows, url) {
        return "<h4>" + escapeHtml(title) + "</h4>"
            + (subtitle ? "<p class='map-popup__lead'>" + escapeHtml(subtitle) + "</p>" : "")
            + rows.join("")
            + (url ? "<p class='map-popup__link'><a href='" + url + "'>Открыть карточку →</a></p>" : "");
    }

    /* Состав карточки задаётся отдельно для каждого слоя: свойства объекта
       описаны в реестре слоёв на стороне сервера, здесь — их подача. */
    var cards = {
        objects: function (p, id) {
            return card(p.name, [p.type, p.district].filter(Boolean).join(" · "), [
                row("Адрес", p.address),
                row("Площадь", number(p.area) ? number(p.area) + " м²" : null),
                row("Мощность хранения", number(p.capacity) ? number(p.capacity) + " т" : null),
                row("Режим работы", p.hours),
                row("Оператор", p.operator)
            ], "/objects/" + id + "/");
        },
        footprints: function (p, id) {
            return card(p.name, "Контур объекта", [
                row("Площадь", number(p.area) ? number(p.area) + " м²" : null)
            ], "/objects/" + id + "/");
        },
        roads: function (p, id) {
            return card(p.name, p.class_label, [
                row("Состояние", p.state_label),
                row("Загруженность", p.congestion === undefined ? null : p.congestion + " из 10"),
                row("Полос", p.lanes),
                row("Протяжённость", number(p.length_km, 1) ? number(p.length_km, 1) + " км" : null),
                row("Грузовой каркас", p.freight_frame === undefined
                    ? "сведений нет"
                    : (p.freight_frame ? "входит" : "не входит"))
            ], "/roads/" + id + "/");
        },
        incidents: function (p, id) {
            return card(p.type_label, p.road, [
                row("Серьёзность", p.severity_label),
                row("Состояние", p.is_open ? "не закрыто" : "закрыто"),
                row("Грузовое движение", p.affects_cargo ? "затрагивает" : "не затрагивает")
            ], "/incidents/" + id + "/");
        },
        routes: function (p, id) {
            return card(p.name, p.type_label, [
                row("Протяжённость", number(p.distance_km, 1) ? number(p.distance_km, 1) + " км" : null),
                row("Грузовых в сутки", number(p.trucks))
            ], "/routes/" + id + "/");
        },
        zones: function (p) {
            return card(p.name, "Зона ограничения движения", [
                row("Пропуск требуется при РММ от", p.permit_from_tons + " т"),
                row("Сезонное ограничение при РММ от",
                    p.seasonal_from_tons ? p.seasonal_from_tons + " т" : null),
                row("Экологический класс не ниже",
                    p.eco_class ? "Евро-" + p.eco_class : null),
                row("Штраф", number(p.fine) ? number(p.fine) + " ₽" : null)
            ], "/methodology/");
        },
        "districts-fill": function (p, id) {
            var rows = [
                row("Объектов", number(p.objects)),
                row("Площадь", number(p.area_sq_km, 1) ? number(p.area_sq_km, 1) + " км²" : null),
                row("Население", number(p.population) ? number(p.population) + " чел." : null),
                row("Индекс нагрузки", p.index === undefined ? null : p.index + " из 100"),
                row("Место по индексу", p.rank),
                row("Загруженность сети", p.congestion === undefined ? null : p.congestion + " из 10")
            ];
            return card(p.name, p.short_name, rows, "/districts/" + id + "/");
        }
    };

    var popup = new maplibregl.Popup({ closeButton: true, maxWidth: "320px" });

    /* Режим указания точки: пока он включён, щелчок по карте принадлежит
       инструменту расчёта, а не карточке объекта. */
    var pointer = null;

    Object.keys(cards).forEach(function (layer) {
        map.on("click", layer, function (event) {
            // В режиме указания точки щелчок принадлежит инструменту:
            // карточка объекта закрыла бы собой то место, куда целятся.
            if (pointer) return;
            var feature = event.features && event.features[0];
            if (!feature) return;
            popup
                .setLngLat(event.lngLat)
                .setHTML(cards[layer](feature.properties, feature.id))
                .addTo(map);
        });
        map.on("mouseenter", layer, function () {
            map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, function () {
            map.getCanvas().style.cursor = "";
        });
    });

    /* ----------------------------------------------------------------------
       Управление слоями и отбором
       ---------------------------------------------------------------------- */

    // Переключатель панели → слои стиля, которыми он управляет.
    var groups = {
        objects: ["objects", "footprints"],
        roads: ["roads"],
        frame: ["freight-frame"],
        incidents: ["incidents"],
        routes: ["routes"],
        districts: ["districts-fill", "districts-outline"],
        backdrop: ["water", "green"],
        zones: ["zones-outline"]
    };

    function setVisible(group, visible) {
        (groups[group] || []).forEach(function (layer) {
            if (map.getLayer(layer)) {
                map.setLayoutProperty(layer, "visibility", visible ? "visible" : "none");
            }
        });
        if (group === "districts") toggleLabels(visible);
    }

    var toggles = Array.prototype.slice.call(document.querySelectorAll("[data-layer]"));

    function applyToggles() {
        toggles.forEach(function (input) {
            setVisible(input.getAttribute("data-layer"), input.checked);
        });
    }

    toggles.forEach(function (input) {
        input.addEventListener("change", function () {
            setVisible(input.getAttribute("data-layer"), input.checked);
        });
    });

    /* Отбор выполняется выражением стиля: тайл уже получен, и объекты,
       не отвечающие условию, просто перестают рисоваться. */
    var filters = { district: "", type: "" };

    function applyFilters() {
        var conditions = ["all"];
        if (filters.district) conditions.push(["==", ["get", "district"], filters.district]);
        if (filters.type) conditions.push(["==", ["get", "type_code"], filters.type]);
        var expression = conditions.length > 1 ? conditions : null;
        if (map.getLayer("objects")) map.setFilter("objects", expression);
        if (map.getLayer("footprints")) {
            map.setFilter("footprints", filters.type
                ? ["==", ["get", "type_code"], filters.type]
                : null);
        }
    }

    Array.prototype.slice.call(document.querySelectorAll("[data-map-filter]")).forEach(
        function (control) {
            control.addEventListener("change", function () {
                filters[control.getAttribute("data-map-filter")] = control.value;
                applyFilters();
            });
        }
    );

    var metricControl = document.getElementById("m-metric");
    if (metricControl) {
        metricControl.addEventListener("change", function () {
            currentMetric = metrics.filter(function (item) {
                return item.key === metricControl.value;
            })[0] || currentMetric;
            if (map.getLayer("districts-fill")) {
                map.setPaintProperty("districts-fill", "fill-color", districtFill(palette));
            }
            var legend = document.getElementById("map-legend-metric");
            if (legend && currentMetric) {
                legend.textContent = currentMetric.title + ", до "
                    + Number(currentMetric.max).toLocaleString("ru-RU") + " " + currentMetric.unit;
            }
        });
    }

    /* ----------------------------------------------------------------------
       Инструменты, работающие по указанной на карте точке
       ---------------------------------------------------------------------- */

    /* Все три инструмента — «что рядом», доступность и маршрут — устроены
       одинаково: кнопка переводит карту в режим указания, накопленные точки
       передаются обработчику. Режимы взаимно исключают друг друга: иначе
       один щелчок по карте запускал бы сразу несколько расчётов. */

    function stopPointing() {
        if (pointer && pointer.button) pointer.button.classList.remove("button--active");
        pointer = null;
        map.getCanvas().style.cursor = "";
    }

    function startPointing(button, needed, output, prompt, done) {
        var running = pointer && pointer.button === button;
        stopPointing();
        if (running) return;

        pointer = { button: button, needed: needed, points: [], done: done };
        button.classList.add("button--active");
        map.getCanvas().style.cursor = "crosshair";
        if (output) output.innerHTML = "<p class='small faint'>" + prompt + "</p>";
    }

    map.on("click", function (event) {
        if (!pointer) return;
        pointer.points.push([event.lngLat.lng, event.lngLat.lat]);
        if (pointer.points.length < pointer.needed) return;

        var collected = pointer.points;
        var handler = pointer.done;
        stopPointing();
        handler(collected);
    });

    /** Отметить точку на карте и вернуть созданный указатель. */
    function marker(point, colour) {
        return new maplibregl.Marker({ color: colour || palette.ink })
            .setLngLat(point)
            .addTo(map);
    }

    function clearMarkers(list) {
        list.forEach(function (item) { item.remove(); });
        return [];
    }

    /* Отказ показывается словами службы: «точка не привязана к дороге»
       и «маршрутизатор не настроен» — разные обстоятельства, и подменять
       их общим «не получилось» значит скрыть от пользователя причину. */
    function explainFailure(output, response) {
        return response.json().then(function (payload) {
            output.innerHTML = "<p class='small faint'>"
                + escapeHtml(payload.error || "Расчёт не выполнен") + "</p>";
        }).catch(function () {
            output.innerHTML = "<p class='small faint'>Расчёт не выполнен.</p>";
        });
    }

    /* --- Что рядом с точкой ------------------------------------------------ */

    var probeButton = document.getElementById("map-probe");
    var probeResults = document.getElementById("probe-results");
    var probeMarkers = [];

    function showProbe(payload) {
        if (!payload.results.length) {
            probeResults.innerHTML = "<p class='small faint'>В радиусе "
                + payload.radius_km + " км объектов нет.</p>";
        } else {
            probeResults.innerHTML = "<ol class='probe-list'>"
                + payload.results.map(function (item) {
                    return "<li><a href='" + item.url + "'>" + escapeHtml(item.name) + "</a>"
                        + "<span class='faint'> — " + item.distance_km.toLocaleString("ru-RU")
                        + " км</span></li>";
                }).join("")
                + "</ol>";
        }

        var source = map.getSource("probe");
        if (source) {
            source.setData({
                type: "FeatureCollection",
                features: payload.results.map(function (item) {
                    return {
                        type: "Feature",
                        geometry: { type: "Point", coordinates: [item.lon, item.lat] },
                        properties: { name: item.name }
                    };
                })
            });
        }
    }

    function probeAt(points) {
        var radiusControl = document.getElementById("probe-radius");
        var radius = radiusControl ? radiusControl.value : 3;
        var address = settings.urls.nearby
            + "?lon=" + points[0][0].toFixed(6)
            + "&lat=" + points[0][1].toFixed(6)
            + "&radius=" + encodeURIComponent(radius);

        probeMarkers = clearMarkers(probeMarkers);
        probeMarkers.push(marker(points[0]));
        probeResults.innerHTML = "<p class='small faint'>Идёт поиск…</p>";

        fetch(address, { headers: { Accept: "application/json" } })
            .then(function (response) { return response.json(); })
            .then(showProbe)
            .catch(function () {
                probeResults.innerHTML = "<p class='small faint'>Поиск не выполнен.</p>";
            });
    }

    if (probeButton && probeResults) {
        probeButton.addEventListener("click", function () {
            startPointing(probeButton, 1, probeResults, "Укажите точку на карте.", probeAt);
        });
    }

    /* --- Доступность и маршрут по графу дорог ------------------------------ */

    var isochroneButton = document.getElementById("map-isochrone");
    var routeButton = document.getElementById("map-route");
    var routingResults = document.getElementById("routing-results");
    var routingMarkers = [];

    function controlValue(id) {
        var control = document.getElementById(id);
        return control ? control.value : "";
    }

    function showIsochrones(payload) {
        var source = map.getSource("isochrones");
        if (source) source.setData(payload);

        var rows = payload.features.map(function (feature) {
            var properties = feature.properties;
            return row(properties.minutes + " мин",
                       number(properties.area_sq_km, 1) + " км²");
        }).join("");

        routingResults.innerHTML = "<p class='small faint'>"
            + escapeHtml(payload.profile.title) + "</p>" + rows
            + "<p class='small faint' style='margin-top:var(--sp-2)'>"
            + "Площадь территории, достижимой по дорогам за указанное время.</p>";
    }

    function requestIsochrones(points) {
        routingMarkers = clearMarkers(routingMarkers);
        routingMarkers.push(marker(points[0], palette.accent));
        routingResults.innerHTML = "<p class='small faint'>Расчёт по графу дорог…</p>";

        var address = settings.urls.isochrones
            + "?point=" + points[0][0].toFixed(6) + "," + points[0][1].toFixed(6)
            + "&profile=" + encodeURIComponent(controlValue("m-profile"))
            + "&minutes=" + encodeURIComponent(controlValue("m-minutes"));

        fetch(address, { headers: { Accept: "application/json" } })
            .then(function (response) {
                if (!response.ok) return explainFailure(routingResults, response);
                return response.json().then(showIsochrones);
            })
            .catch(function () {
                routingResults.innerHTML = "<p class='small faint'>Расчёт не выполнен.</p>";
            });
    }

    function showRoute(payload) {
        var source = map.getSource("route");
        if (source) {
            source.setData({
                type: "Feature",
                geometry: payload.geometry,
                properties: {}
            });
        }

        var rows = [
            row("Протяжённость", number(payload.distance_km, 1) + " км"),
            row("Время в пути", number(payload.duration_min, 0) + " мин"),
            row("Зоны ограничения",
                payload.zones.length ? payload.zones.join(", ") : "не задеты"),
            row("Пропуск", payload.permit || "не требуется"),
            row("Штраф за нарушение",
                payload.fine_rubles ? number(payload.fine_rubles) + " ₽" : null)
        ].join("");

        var warnings = (payload.prohibitions || []).map(function (item) {
            return "<p class='small' style='color:var(--tone-alert)'>"
                + escapeHtml(item) + "</p>";
        }).join("");

        routingResults.innerHTML = "<p class='small'>" + escapeHtml(payload.summary) + "</p>"
            + rows + warnings;
    }

    function requestRoute(points) {
        routingMarkers = clearMarkers(routingMarkers);
        points.forEach(function (point, index) {
            routingMarkers.push(marker(point, index ? palette.alert : palette.ok));
        });
        routingResults.innerHTML = "<p class='small faint'>Прокладка маршрута…</p>";

        var address = settings.urls.route
            + "?from=" + points[0][0].toFixed(6) + "," + points[0][1].toFixed(6)
            + "&to=" + points[1][0].toFixed(6) + "," + points[1][1].toFixed(6)
            + "&profile=" + encodeURIComponent(controlValue("m-profile"));

        fetch(address, { headers: { Accept: "application/json" } })
            .then(function (response) {
                if (!response.ok) return explainFailure(routingResults, response);
                return response.json().then(showRoute);
            })
            .catch(function () {
                routingResults.innerHTML = "<p class='small faint'>Расчёт не выполнен.</p>";
            });
    }

    if (isochroneButton && routingResults) {
        isochroneButton.addEventListener("click", function () {
            startPointing(
                isochroneButton, 1, routingResults,
                "Укажите точку, от которой считать доступность.", requestIsochrones
            );
        });
    }

    if (routeButton && routingResults) {
        routeButton.addEventListener("click", function () {
            startPointing(
                routeButton, 2, routingResults,
                "Укажите начало и конец маршрута.", requestRoute
            );
        });
    }

    /* ----------------------------------------------------------------------
       Смена оформления
       ---------------------------------------------------------------------- */

    /* Стиль собирается заново теми же цветами, что и остальной интерфейс.
       Тайлы при этом не запрашиваются повторно — они уже получены. */
    function applyTheme() {
        palette = ffBasemap.palette();
        map.setStyle(buildStyle(palette));
        map.once("styledata", function () {
            applyToggles();
            applyFilters();
        });
    }

    new MutationObserver(applyTheme).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"]
    });

    map.on("load", function () {
        applyToggles();
        applyFilters();
        node.setAttribute("data-map-ready", "1");
    });
})();
