/* ==========================================================================
   ИС «ГрузПоток» — интерактивная карта

   Карта строится на библиотеке Leaflet, размещённой локально в составе
   проекта: внешние сети при работе системы не требуются, если используется
   собственный тайловый сервер.

   Слои загружаются независимо и по требованию: включение слоя вызывает
   запрос к соответствующей конечной точке GeoJSON, повторное включение
   использует уже полученные данные. Такой порядок ограничивает объём
   передаваемых данных тем, что пользователь действительно смотрит.
   ========================================================================== */

(function () {
    "use strict";

    var node = document.getElementById("map-canvas");
    if (!node || typeof L === "undefined") return;

    /* Настройки приходят отдельным элементом application/json, собранным на
       стороне сервера. Разбор в data-атрибуте разметки был источником отказа:
       координаты подставлялись через локализацию и приходили с десятичной
       запятой. */
    var settingsNode = document.getElementById("map-settings");
    if (!settingsNode) return;

    var settings;
    try {
        settings = JSON.parse(settingsNode.textContent);
    } catch (error) {
        return;
    }

    /* Цвета берутся из переменных оформления: при переключении темы карта
       перекрашивается теми же значениями, что и остальной интерфейс. */
    function themeColor(name, fallback) {
        var value = getComputedStyle(document.documentElement)
            .getPropertyValue(name)
            .trim();
        return value || fallback;
    }

    var palette = {
        accent: themeColor("--accent", "#f2a03d"),
        ok: themeColor("--tone-ok", "#3fbf6f"),
        warn: themeColor("--tone-warn", "#f2a03d"),
        alert: themeColor("--tone-alert", "#e2504a"),
        crit: themeColor("--tone-crit", "#a32b26"),
        muted: themeColor("--tone-muted", "#6b7885"),
        route: themeColor("--series-2", "#4aa3d9")
    };

    function toneColor(tone) {
        return palette[tone] || palette.muted;
    }

    /* ----------------------------------------------------------------------
       Инициализация карты
       ---------------------------------------------------------------------- */

    var map = L.map(node, {
        center: settings.center || [55.7522, 37.6156],
        zoom: settings.zoom || 10,
        zoomControl: true,
        preferCanvas: true
    });

    var lightTiles = L.tileLayer(settings.tileUrl, {
        attribution: settings.attribution,
        maxZoom: 19
    });
    var darkTiles = L.tileLayer(settings.tileUrlDark || settings.tileUrl, {
        attribution: settings.attribution,
        maxZoom: 19
    });

    /** Выбрать подложку, соответствующую текущему оформлению интерфейса. */
    function applyBasemap() {
        var dark = document.documentElement.getAttribute("data-theme") !== "light";
        var active = dark ? darkTiles : lightTiles;
        var inactive = dark ? lightTiles : darkTiles;
        if (map.hasLayer(inactive)) map.removeLayer(inactive);
        if (!map.hasLayer(active)) active.addTo(map);
    }
    applyBasemap();

    /* Смена темы отслеживается наблюдателем: подложка меняется без
       перезагрузки страницы. */
    new MutationObserver(applyBasemap).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"]
    });

    /* ----------------------------------------------------------------------
       Оформление объектов слоёв
       ---------------------------------------------------------------------- */

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    function popupRow(label, value) {
        if (value === null || value === undefined || value === "") return "";
        return "<div style='display:flex;justify-content:space-between;gap:12px'>"
            + "<span style='opacity:.7'>" + escapeHtml(label) + "</span>"
            + "<span>" + escapeHtml(value) + "</span></div>";
    }

    function popupShell(title, subtitle, rows, url) {
        return "<h4>" + escapeHtml(title) + "</h4>"
            + (subtitle ? "<p style='opacity:.7;margin-bottom:8px'>" + escapeHtml(subtitle) + "</p>" : "")
            + rows.join("")
            + (url ? "<p style='margin-top:10px'><a href='" + url + "'>Открыть карточку →</a></p>" : "");
    }

    /* ----------------------------------------------------------------------
       Описание слоёв
       ---------------------------------------------------------------------- */

    var layerDefs = {
        objects: {
            url: settings.urls.objects,
            build: function (data) {
                return L.geoJSON(data, {
                    pointToLayer: function (feature, latlng) {
                        return L.circleMarker(latlng, {
                            radius: 6,
                            color: palette.accent,
                            weight: 1.5,
                            fillColor: palette.accent,
                            fillOpacity: 0.55
                        });
                    },
                    onEachFeature: function (feature, layer) {
                        var p = feature.properties;
                        layer.bindPopup(popupShell(p.name, p.type + " · " + p.district, [
                            popupRow("Адрес", p.address),
                            popupRow("Мощность", p.capacity ? p.capacity.toLocaleString("ru-RU") + " т" : null),
                            popupRow("Площадь", p.area ? p.area.toLocaleString("ru-RU") + " м²" : null),
                            popupRow("Режим работы", p.hours)
                        ], p.url));
                    }
                });
            }
        },

        roads: {
            url: settings.urls.roads,
            build: function (data) {
                return L.geoJSON(data, {
                    style: function (feature) {
                        return {
                            color: toneColor(feature.properties.tone),
                            weight: 4,
                            opacity: 0.85
                        };
                    },
                    onEachFeature: function (feature, layer) {
                        var p = feature.properties;
                        layer.bindPopup(popupShell(p.name, p.road_class, [
                            popupRow("Загруженность", p.congestion !== null
                                ? p.congestion + " из 10 · " + p.state_label : "нет данных"),
                            popupRow("Скорость потока", p.speed ? p.speed + " км/ч" : null),
                            popupRow("Разрешённая скорость", p.speed_limit ? p.speed_limit + " км/ч" : null),
                            popupRow("Полос", p.lanes),
                            popupRow("Протяжённость", p.length ? p.length + " км" : null)
                        ], p.url));
                    }
                });
            }
        },

        routes: {
            url: settings.urls.routes,
            build: function (data) {
                return L.geoJSON(data, {
                    style: {
                        color: palette.route,
                        weight: 3,
                        opacity: 0.8,
                        dashArray: "8 6"
                    },
                    onEachFeature: function (feature, layer) {
                        var p = feature.properties;
                        layer.bindPopup(popupShell(p.name, p.route_type_label, [
                            popupRow("Протяжённость", p.distance ? p.distance + " км" : null),
                            popupRow("Интенсивность", p.trucks ? p.trucks + " ТС/сут" : null)
                        ], p.url));
                    }
                });
            }
        },

        incidents: {
            url: settings.urls.incidents,
            build: function (data) {
                return L.geoJSON(data, {
                    pointToLayer: function (feature, latlng) {
                        var p = feature.properties;
                        return L.circleMarker(latlng, {
                            radius: 5 + p.severity,
                            color: toneColor(p.tone),
                            weight: 2,
                            fillColor: toneColor(p.tone),
                            fillOpacity: p.is_open ? 0.5 : 0.15
                        });
                    },
                    onEachFeature: function (feature, layer) {
                        var p = feature.properties;
                        layer.bindPopup(popupShell(p.type_label, p.road, [
                            popupRow("Серьёзность", p.severity + " · " + p.severity_label),
                            popupRow("Состояние", p.is_open ? "открыт" : "устранён"),
                            popupRow("Грузовой транспорт", p.affects_cargo ? "затронут" : "не затронут"),
                            popupRow("Описание", p.description)
                        ], p.url));
                    }
                });
            }
        },

        districts: {
            url: settings.urls.districts,
            build: function (data) {
                return L.geoJSON(data, {
                    pointToLayer: function (feature, latlng) {
                        var p = feature.properties;
                        /* Округ обозначается подписью-меткой: величина
                           показателя читается прямо на карте, без нажатия. */
                        return L.marker(latlng, {
                            icon: L.divIcon({
                                className: "",
                                html: "<div style=\"white-space:nowrap;padding:3px 8px;"
                                    + "border-radius:4px;font-size:11px;font-weight:600;"
                                    + "background:" + toneColor(p.tone) + ";color:#0e1216\">"
                                    + escapeHtml(p.short_name) + " · " + p.objects + "</div>",
                                iconSize: null
                            })
                        });
                    },
                    onEachFeature: function (feature, layer) {
                        var p = feature.properties;
                        layer.bindPopup(popupShell(p.name + " округ", null, [
                            popupRow("Объектов", p.objects),
                            popupRow("Мощность", Math.round(p.capacity).toLocaleString("ru-RU") + " т"),
                            popupRow("Грузопоток", Math.round(p.volume).toLocaleString("ru-RU") + " т"),
                            popupRow("Загруженность", p.congestion)
                        ], p.url));
                    }
                });
            }
        }
    };

    var loaded = {};
    var active = {};

    /** Загрузить слой и добавить его на карту. */
    function enableLayer(name) {
        var def = layerDefs[name];
        if (!def) return;

        if (loaded[name]) {
            loaded[name].addTo(map);
            active[name] = true;
            return;
        }

        setStatus(name, "загрузка…");
        var url = def.url + buildQuery();

        fetch(url, { headers: { "X-Requested-With": "fetch" } })
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (data) {
                var layer = def.build(data);
                loaded[name] = layer;
                if (active[name] !== false) layer.addTo(map);
                setStatus(name, data.count + " объектов");
            })
            .catch(function () {
                setStatus(name, "не загружен");
            });
        active[name] = true;
    }

    function disableLayer(name) {
        active[name] = false;
        if (loaded[name]) map.removeLayer(loaded[name]);
    }

    function setStatus(name, text) {
        var badge = document.querySelector("[data-layer-status='" + name + "']");
        if (badge) badge.textContent = text;
    }

    /** Собрать строку запроса из значений панели условий отбора. */
    function buildQuery() {
        var params = new URLSearchParams();
        document.querySelectorAll("[data-map-filter]").forEach(function (control) {
            if (control.value) params.set(control.dataset.mapFilter, control.value);
        });
        var query = params.toString();
        return query ? "?" + query : "";
    }

    /* ----------------------------------------------------------------------
       Панель управления слоями
       ---------------------------------------------------------------------- */

    document.querySelectorAll("[data-layer]").forEach(function (checkbox) {
        if (checkbox.checked) enableLayer(checkbox.dataset.layer);
        checkbox.addEventListener("change", function () {
            if (checkbox.checked) {
                enableLayer(checkbox.dataset.layer);
            } else {
                disableLayer(checkbox.dataset.layer);
            }
        });
    });

    /* Изменение условий отбора сбрасывает загруженные слои: данные должны
       соответствовать выбранным параметрам. */
    document.querySelectorAll("[data-map-filter]").forEach(function (control) {
        control.addEventListener("change", function () {
            Object.keys(loaded).forEach(function (name) {
                map.removeLayer(loaded[name]);
                delete loaded[name];
            });
            document.querySelectorAll("[data-layer]").forEach(function (checkbox) {
                if (checkbox.checked) enableLayer(checkbox.dataset.layer);
            });
        });
    });

    /* ----------------------------------------------------------------------
       Инструмент «что рядом»
       ---------------------------------------------------------------------- */

    var probeButton = document.getElementById("map-probe");
    var probeResults = document.getElementById("probe-results");
    var probeMarker = null;
    var probeCircle = null;
    var probing = false;

    if (probeButton) {
        probeButton.addEventListener("click", function () {
            probing = !probing;
            probeButton.classList.toggle("button--primary", probing);
            probeButton.textContent = probing
                ? "Укажите точку на карте"
                : "Что рядом с точкой";
            node.style.cursor = probing ? "crosshair" : "";
        });
    }

    map.on("click", function (event) {
        if (!probing) return;

        var radius = Number(document.getElementById("probe-radius").value || 3);
        if (probeMarker) map.removeLayer(probeMarker);
        if (probeCircle) map.removeLayer(probeCircle);

        probeMarker = L.circleMarker(event.latlng, {
            radius: 5, color: palette.accent, fillColor: palette.accent, fillOpacity: 1
        }).addTo(map);
        probeCircle = L.circle(event.latlng, {
            radius: radius * 1000,
            color: palette.accent,
            weight: 1,
            fillOpacity: 0.06
        }).addTo(map);

        probeResults.innerHTML = "<p class='small muted'>Поиск объектов…</p>";

        fetch(settings.urls.nearby + "?lon=" + event.latlng.lng
            + "&lat=" + event.latlng.lat + "&radius=" + radius + "&limit=15")
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (!data.count) {
                    probeResults.innerHTML =
                        "<p class='small muted'>В радиусе " + radius
                        + " км объектов не найдено. Увеличьте радиус или укажите другую точку.</p>";
                    return;
                }
                probeResults.innerHTML =
                    "<p class='eyebrow' style='margin-bottom:8px'>Найдено: " + data.count + "</p>"
                    + data.results.map(function (item) {
                        return "<a href='" + item.url + "' class='row row--between small' "
                            + "style='color:inherit;padding:5px 0;border-bottom:1px solid var(--border)'>"
                            + "<span>" + escapeHtml(item.name)
                            + "<span class='table-sub'>" + escapeHtml(item.type) + "</span></span>"
                            + "<span class='mono faint'>" + item.distance_km.toFixed(1) + " км</span></a>";
                    }).join("");
            })
            .catch(function () {
                probeResults.innerHTML =
                    "<p class='small muted'>Не удалось выполнить поиск. Повторите попытку.</p>";
            });
    });

    /* Карта доступна прочим сценариям страницы — например, для перехода к
       объекту, переданному в адресной строке. */
    window.FreightFlowMap = { map: map, enableLayer: enableLayer, palette: palette };
})();
