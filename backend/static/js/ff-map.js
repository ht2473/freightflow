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
    if (!node || typeof maplibregl === "undefined") return;

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

    function themeColor(name, fallback) {
        var value = getComputedStyle(document.documentElement)
            .getPropertyValue(name)
            .trim();
        return value || fallback;
    }

    /** Прочитать палитру заново: при смене оформления значения меняются. */
    function readPalette() {
        return {
            canvas: themeColor("--surface-sunken", "#eef1f4"),
            land: themeColor("--surface", "#ffffff"),
            ink: themeColor("--ink", "#1c2733"),
            accent: themeColor("--accent", "#f2a03d"),
            ok: themeColor("--tone-ok", "#3fbf6f"),
            warn: themeColor("--tone-warn", "#f2a03d"),
            alert: themeColor("--tone-alert", "#e2504a"),
            crit: themeColor("--tone-crit", "#a32b26"),
            muted: themeColor("--tone-muted", "#6b7885"),
            route: themeColor("--series-2", "#4aa3d9"),
            line: themeColor("--border", "#c8d0d8")
        };
    }

    var palette = readPalette();

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
        return {
            version: 8,
            sources: {
                freightflow: {
                    type: "vector",
                    url: settings.urls.tilejson,
                    attribution: settings.attribution
                },
                probe: {
                    type: "geojson",
                    data: { type: "FeatureCollection", features: [] }
                }
            },
            layers: [
                {
                    id: "canvas",
                    type: "background",
                    paint: { "background-color": colors.canvas }
                },
                {
                    id: "districts-fill",
                    type: "fill",
                    source: SOURCE,
                    "source-layer": "districts",
                    paint: { "fill-color": districtFill(colors), "fill-opacity": 0.55 }
                },
                {
                    id: "districts-outline",
                    type: "line",
                    source: SOURCE,
                    "source-layer": "districts",
                    paint: {
                        "line-color": colors.ink,
                        "line-width": 1,
                        "line-opacity": 0.35
                    }
                },
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
                    id: "roads",
                    type: "line",
                    source: SOURCE,
                    "source-layer": "roads",
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": toneMatch(colors),
                        "line-width": ["interpolate", ["linear"], ["zoom"], 7, 1, 12, 3, 16, 7]
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
                        "circle-opacity": 0.8,
                        "circle-stroke-width": 1,
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
            ]
        };
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

    Object.keys(cards).forEach(function (layer) {
        map.on("click", layer, function (event) {
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
        incidents: ["incidents"],
        routes: ["routes"],
        districts: ["districts-fill", "districts-outline"],
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
       Инструмент «что рядом с точкой»
       ---------------------------------------------------------------------- */

    var probeButton = document.getElementById("map-probe");
    var probeResults = document.getElementById("probe-results");
    var probeMarker = null;
    var probing = false;

    function probeSource() {
        return map.getSource("probe");
    }

    function showProbe(payload) {
        if (!probeResults) return;
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

        var source = probeSource();
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

    function probeAt(lngLat) {
        var radiusControl = document.getElementById("probe-radius");
        var radius = radiusControl ? radiusControl.value : 3;
        var address = settings.urls.nearby
            + "?lon=" + lngLat.lng.toFixed(6)
            + "&lat=" + lngLat.lat.toFixed(6)
            + "&radius=" + encodeURIComponent(radius);

        if (probeMarker) probeMarker.remove();
        probeMarker = new maplibregl.Marker({ color: palette.ink })
            .setLngLat(lngLat)
            .addTo(map);

        if (probeResults) probeResults.innerHTML = "<p class='small faint'>Идёт поиск…</p>";
        fetch(address, { headers: { Accept: "application/json" } })
            .then(function (response) { return response.json(); })
            .then(showProbe)
            .catch(function () {
                if (probeResults) {
                    probeResults.innerHTML = "<p class='small faint'>Поиск не выполнен.</p>";
                }
            });
    }

    if (probeButton) {
        probeButton.addEventListener("click", function () {
            probing = !probing;
            probeButton.classList.toggle("button--active", probing);
            map.getCanvas().style.cursor = probing ? "crosshair" : "";
            if (probeResults && probing) {
                probeResults.innerHTML =
                    "<p class='small faint'>Укажите точку на карте.</p>";
            }
        });
    }

    map.on("click", function (event) {
        if (!probing) return;
        probing = false;
        if (probeButton) probeButton.classList.remove("button--active");
        map.getCanvas().style.cursor = "";
        probeAt(event.lngLat);
    });

    /* ----------------------------------------------------------------------
       Смена оформления
       ---------------------------------------------------------------------- */

    /* Стиль собирается заново теми же цветами, что и остальной интерфейс.
       Тайлы при этом не запрашиваются повторно — они уже получены. */
    function applyTheme() {
        palette = readPalette();
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
