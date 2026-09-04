/* ==========================================================================
   ИС «ГрузПоток» — карта на карточке объекта

   Небольшая карта, показывающая положение одной записи: точку объекта или
   события, ломаную магистрали или коридора. Подложка та же, что и у карты
   раздела (ff-basemap.js), и приходит теми же векторными тайлами.

   Взаимодействие ограничено намеренно: карточка отвечает на вопрос «где
   это находится», а разбор окрестностей ведётся на карте раздела.
   ========================================================================== */

(function () {
    "use strict";

    var node = document.getElementById("minimap");
    if (!node || typeof maplibregl === "undefined" || !window.ffBasemap) return;

    var settingsNode = document.getElementById("minimap-settings");
    if (!settingsNode) return;

    var settings;
    try {
        settings = JSON.parse(settingsNode.textContent);
    } catch (error) {
        return;
    }

    var palette = window.ffBasemap.palette();

    /** Слой самой записи: точка либо ломаная. */
    function featureLayers(colors) {
        if (settings.geometry && settings.geometry.type !== "Point") {
            return [
                {
                    id: "feature-line",
                    type: "line",
                    source: "feature",
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": colors.accent,
                        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 3, 15, 8]
                    }
                }
            ];
        }
        return [
            {
                id: "feature-point",
                type: "circle",
                source: "feature",
                paint: {
                    "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 6, 16, 12],
                    "circle-color": colors.accent,
                    "circle-opacity": 0.75,
                    "circle-stroke-width": 2,
                    "circle-stroke-color": colors.land
                }
            }
        ];
    }

    /* Соседние объекты и их контуры: без них крупный масштаб показывает
       пустоту — магистральная сеть на уровне квартала уже кончилась,
       а сама запись висит в ней одна. */
    function surroundings(colors) {
        return [
            {
                id: "footprints",
                type: "fill",
                source: "freightflow",
                "source-layer": "footprints",
                minzoom: 14,
                paint: { "fill-color": colors.muted, "fill-opacity": 0.35 }
            },
            {
                id: "objects",
                type: "circle",
                source: "freightflow",
                "source-layer": "objects",
                paint: {
                    "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2, 16, 5],
                    "circle-color": colors.muted,
                    "circle-opacity": 0.8
                }
            }
        ];
    }

    function buildStyle(colors) {
        var style = window.ffBasemap.style(colors, {
            tilejson: settings.tilejson,
            attribution: settings.attribution
        });
        style.layers = style.layers.concat(surroundings(colors));
        style.sources.feature = {
            type: "geojson",
            data: { type: "Feature", geometry: settings.geometry, properties: {} }
        };
        style.layers = style.layers.concat(featureLayers(colors));
        return style;
    }

    var map;
    try {
        map = new maplibregl.Map({
            container: node,
            style: buildStyle(palette),
            center: settings.center,
            zoom: settings.zoom,
            minZoom: settings.minZoom,
            maxZoom: settings.maxZoom,
            attributionControl: false,
            // Прокрутка страницы важнее приближения карты: карточка читается
            // сверху вниз, и перехват колеса мешал бы этому.
            scrollZoom: false
        });
    } catch (error) {
        node.innerHTML = "<p class='small faint' style='padding:var(--sp-4)'>"
            + "Карта требует поддержки WebGL. Координаты приведены в таблице ниже.</p>";
        return;
    }

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(
        new maplibregl.AttributionControl({ compact: true, customAttribution: settings.attribution })
    );

    /* Ломаная магистрали протянута через полгорода: показывать её от края
       до края осмысленнее, чем центрировать по середине. */
    if (settings.bounds) {
        map.fitBounds(settings.bounds, { padding: 32, animate: false });
    }

    new MutationObserver(function () {
        palette = window.ffBasemap.palette();
        map.setStyle(buildStyle(palette));
    }).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"]
    });

    map.on("load", function () {
        node.setAttribute("data-map-ready", "1");
    });
})();
