/* ==========================================================================
   ИС «ГрузПоток» — общая подложка карт

   Основа стиля, одинаковая для карты раздела и для карт на карточках
   объектов: вода, зелёные массивы, магистральная сеть и границы округов.
   Определение одно, поэтому карта склада выглядит так же, как обзорная
   карта города, и правка цвета не расходится между ними.

   Цвета берутся из переменных оформления: при переключении темы карта
   перекрашивается теми же значениями, что и остальной интерфейс.
   ========================================================================== */

(function (global) {
    "use strict";

    function themeColor(name, fallback) {
        var value = getComputedStyle(document.documentElement)
            .getPropertyValue(name)
            .trim();
        return value || fallback;
    }

    /** Прочитать палитру заново: при смене оформления значения меняются. */
    function palette() {
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
            water: themeColor("--map-water", "#1d3a4d"),
            green: themeColor("--map-green", "#1e3328"),
            line: themeColor("--border", "#c8d0d8")
        };
    }

    /**
     * Собрать основу стиля.
     *
     * Цвет магистралей задаётся вызывающей стороной: на обзорной карте он
     * выражает загруженность, на карточке объекта — только сам факт дороги.
     */
    function style(colors, options) {
        return {
            version: 8,
            sources: {
                freightflow: {
                    type: "vector",
                    url: options.tilejson,
                    attribution: options.attribution
                }
            },
            layers: [
                {
                    id: "canvas",
                    type: "background",
                    paint: { "background-color": colors.canvas }
                },
                {
                    id: "green",
                    type: "fill",
                    source: "freightflow",
                    "source-layer": "green",
                    paint: { "fill-color": colors.green }
                },
                {
                    id: "water",
                    type: "fill",
                    source: "freightflow",
                    "source-layer": "water",
                    paint: { "fill-color": colors.water }
                },
                {
                    id: "roads",
                    type: "line",
                    source: "freightflow",
                    "source-layer": "roads",
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": options.roadColor || colors.muted,
                        "line-width": ["interpolate", ["linear"], ["zoom"], 7, 1, 12, 3, 16, 7]
                    }
                },
                {
                    id: "districts-outline",
                    type: "line",
                    source: "freightflow",
                    "source-layer": "districts",
                    paint: {
                        "line-color": colors.ink,
                        "line-width": 1,
                        "line-opacity": 0.35
                    }
                }
            ]
        };
    }

    /** Вставить слой перед слоем с указанным обозначением. */
    function insertBefore(layers, beforeId, layer) {
        var position = layers.findIndex(function (item) { return item.id === beforeId; });
        layers.splice(position < 0 ? layers.length : position, 0, layer);
        return layers;
    }

    global.ffBasemap = {
        color: themeColor,
        palette: palette,
        style: style,
        insertBefore: insertBefore
    };
})(window);
