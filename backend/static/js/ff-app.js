/* ==========================================================================
   ИС «ГрузПоток» — клиентская логика интерфейса

   Модуль намеренно написан без внешних зависимостей и фреймворков. Страницы
   приходят с сервера уже отрисованными, а сценарий добавляет к ним поведение:
   переключение оформления, работу меню на узких экранах, построение графиков
   и мелкие взаимодействия в таблицах.

   Такой подход даёт три свойства, важных для системы этого класса:
     • страницы полностью работоспособны до загрузки сценария;
     • объём передаваемых данных минимален;
     • поведение прозрачно и проверяемо без сборщика.
   ========================================================================== */

(function () {
    "use strict";

    /* ----------------------------------------------------------------------
       Оформление: тёмное, светлое, как в системе
       ---------------------------------------------------------------------- */

    var THEME_KEY = "ff-theme";
    var root = document.documentElement;

    /* Подписи режимов. Кнопка сообщает текущий режим, а не будущий: при трёх
       состояниях подпись вида «включить светлое» не позволяет узнать, что
       выбрано сейчас, а это единственный признак режима «как в системе». */
    var THEME_LABELS = {
        dark: root.dataset.themeLabelDark || "Оформление: тёмное",
        light: root.dataset.themeLabelLight || "Оформление: светлое",
        auto: root.dataset.themeLabelAuto || "Оформление: как в системе"
    };

    /** Применить оформление и запомнить выбор. */
    function applyTheme(theme) {
        if (theme === "auto") {
            var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
            root.setAttribute("data-theme", dark ? "dark" : "light");
        } else {
            root.setAttribute("data-theme", theme);
        }
        root.setAttribute("data-theme-choice", theme);
        try {
            localStorage.setItem(THEME_KEY, theme);
        } catch (error) {
            /* Приватный режим браузера: выбор действует до конца сессии. */
        }

        /* Знак режима переключается правилами CSS по атрибуту
           data-theme-choice: управлять видимостью <svg> через свойство hidden
           нельзя — оно определено для элементов HTML, а не SVG. */

        var label = THEME_LABELS[theme] || THEME_LABELS.dark;
        document.querySelectorAll("[data-action='theme']").forEach(function (node) {
            node.setAttribute("aria-label", label);
            node.setAttribute("title", label);
        });
    }

    /** Переключить оформление по кругу: тёмное → светлое → системное. */
    function cycleTheme() {
        var order = ["dark", "light", "auto"];
        var current = root.getAttribute("data-theme-choice") || "dark";
        applyTheme(order[(order.indexOf(current) + 1) % order.length]);
    }

    var stored = null;
    try {
        stored = localStorage.getItem(THEME_KEY);
    } catch (error) {
        stored = null;
    }
    applyTheme(stored || root.dataset.themeDefault || "dark");

    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        if (root.getAttribute("data-theme-choice") === "auto") {
            applyTheme("auto");
        }
    });

    /* ----------------------------------------------------------------------
       Навигация и общие взаимодействия
       ---------------------------------------------------------------------- */

    document.addEventListener("click", function (event) {
        var themeButton = event.target.closest("[data-action='theme']");
        if (themeButton) {
            event.preventDefault();
            cycleTheme();
            return;
        }

        var navButton = event.target.closest("[data-action='nav']");
        if (navButton) {
            event.preventDefault();
            var nav = document.getElementById("main-nav");
            var opened = nav.classList.toggle("is-open");
            navButton.setAttribute("aria-expanded", String(opened));
            return;
        }

        var dismiss = event.target.closest("[data-action='dismiss']");
        if (dismiss) {
            dismiss.closest(".flash").remove();
            return;
        }

        /* Строка таблицы целиком ведёт на карточку записи. Ссылка внутри
           строки при этом продолжает работать обычным образом. */
        var row = event.target.closest("tr[data-href]");
        if (row && !event.target.closest("a, button, input, label")) {
            window.location.href = row.dataset.href;
        }
    });

    /* Форма отбора отправляется сразу при изменении выпадающего списка:
       это избавляет от лишнего нажатия на кнопку в типовом сценарии. */
    document.querySelectorAll("[data-autosubmit] select, [data-autosubmit] input[type=checkbox]")
        .forEach(function (control) {
            control.addEventListener("change", function () {
                control.form.requestSubmit ? control.form.requestSubmit() : control.form.submit();
            });
        });

    /* Сообщения о результате действия скрываются сами через семь секунд —
       достаточно, чтобы прочитать, и не мешает дальнейшей работе. */
    document.querySelectorAll(".flash").forEach(function (flash) {
        window.setTimeout(function () {
            flash.style.transition = "opacity 400ms";
            flash.style.opacity = "0";
            window.setTimeout(function () { flash.remove(); }, 400);
        }, 7000);
    });

    /* Копирование токена доступа и примеров обращения к API. */
    document.querySelectorAll("[data-copy]").forEach(function (button) {
        button.addEventListener("click", function () {
            var source = document.querySelector(button.dataset.copy);
            if (!source) return;
            navigator.clipboard.writeText(source.textContent.trim()).then(function () {
                var original = button.textContent;
                button.textContent = "Скопировано";
                window.setTimeout(function () { button.textContent = original; }, 1800);
            });
        });
    });

    /* ----------------------------------------------------------------------
       Графики: собственная отрисовка средствами SVG

       Отказ от библиотеки визуализации сокращает объём страницы примерно на
       200 КБ и позволяет оформить графики теми же переменными, что и весь
       интерфейс, — в том числе корректно перекрашивать их при смене темы.
       ---------------------------------------------------------------------- */

    var SVG_NS = "http://www.w3.org/2000/svg";

    /** Создать элемент SVG с набором атрибутов. */
    function svg(tag, attributes) {
        var node = document.createElementNS(SVG_NS, tag);
        Object.keys(attributes || {}).forEach(function (key) {
            node.setAttribute(key, attributes[key]);
        });
        return node;
    }

    /** Отформатировать число для подписи оси. */
    function formatTick(value) {
        var absolute = Math.abs(value);
        if (absolute >= 1e9) return (value / 1e9).toFixed(1) + " млрд";
        if (absolute >= 1e6) return (value / 1e6).toFixed(1) + " млн";
        if (absolute >= 1e3) return Math.round(value / 1e3) + " тыс.";
        return String(Math.round(value * 10) / 10);
    }

    /**
     * Линейный график с необязательной прогнозной частью.
     *
     * Ожидаемый формат данных в атрибуте data-chart:
     * {"labels": [...], "series": [{"values": [...], "forecast": false}]}
     */
    function renderLine(container, config) {
        var width = container.clientWidth || 720;
        var height = Number(container.dataset.height || 240);
        var padding = { top: 12, right: 12, bottom: 26, left: 52 };
        var plotWidth = width - padding.left - padding.right;
        var plotHeight = height - padding.top - padding.bottom;

        var allValues = config.series.reduce(function (acc, series) {
            return acc.concat(series.values.filter(function (v) { return v !== null; }));
        }, []);
        if (!allValues.length) return;

        var maxValue = Math.max.apply(null, allValues);
        var minValue = Math.min(0, Math.min.apply(null, allValues));
        var range = maxValue - minValue || 1;
        var count = config.labels.length;

        var chart = svg("svg", {
            class: "chart",
            viewBox: "0 0 " + width + " " + height,
            role: "img",
            "aria-label": config.title || "График"
        });

        var x = function (index) {
            return padding.left + (count > 1 ? (index * plotWidth) / (count - 1) : plotWidth / 2);
        };
        var y = function (value) {
            return padding.top + plotHeight - ((value - minValue) / range) * plotHeight;
        };

        /* Сетка и подписи оси значений. */
        var grid = svg("g", { class: "chart__grid" });
        for (var step = 0; step <= 4; step++) {
            var value = minValue + (range * step) / 4;
            var lineY = y(value);
            grid.appendChild(svg("line", {
                x1: padding.left, y1: lineY, x2: width - padding.right, y2: lineY
            }));
            var label = svg("text", {
                class: "chart__axis", x: padding.left - 8, y: lineY + 3, "text-anchor": "end"
            });
            label.textContent = formatTick(value);
            grid.appendChild(label);
        }
        chart.appendChild(grid);

        /* Подписи оси категорий: выводится не более шести отметок. */
        var stride = Math.max(1, Math.ceil(count / 6));
        for (var i = 0; i < count; i += stride) {
            var tick = svg("text", {
                class: "chart__axis", x: x(i), y: height - 8, "text-anchor": "middle"
            });
            tick.textContent = config.labels[i];
            chart.appendChild(tick);
        }

        config.series.forEach(function (series, seriesIndex) {
            var points = [];
            series.values.forEach(function (value, index) {
                if (value === null || value === undefined) return;
                points.push([x(index), y(value)]);
            });
            if (!points.length) return;

            var path = points.map(function (point, index) {
                return (index ? "L" : "M") + point[0].toFixed(1) + " " + point[1].toFixed(1);
            }).join(" ");

            /* Заливка под первой серией подчёркивает основной ряд. */
            if (!seriesIndex && config.area !== false) {
                var areaPath = path
                    + " L" + points[points.length - 1][0].toFixed(1) + " " + y(minValue)
                    + " L" + points[0][0].toFixed(1) + " " + y(minValue) + " Z";
                chart.appendChild(svg("path", { class: "chart__area", d: areaPath }));
            }

            chart.appendChild(svg("path", {
                class: "chart__line" + (series.forecast ? " chart__line--forecast" : ""),
                d: path,
                style: series.color ? "stroke:" + series.color : ""
            }));

            /* Точки выводятся только на коротких рядах: на длинных они
               сливаются и мешают читать линию. */
            if (points.length <= 24) {
                points.forEach(function (point, index) {
                    var dot = svg("circle", {
                        class: "chart__dot", cx: point[0], cy: point[1], r: 3,
                        style: series.color ? "fill:" + series.color : ""
                    });
                    var title = svg("title");
                    title.textContent = config.labels[index] + ": " + formatTick(series.values[index]);
                    dot.appendChild(title);
                    chart.appendChild(dot);
                });
            }
        });

        container.innerHTML = "";
        container.appendChild(chart);
    }

    /** Столбчатая диаграмма с окраской по семафорной шкале. */
    function renderBar(container, config) {
        var width = container.clientWidth || 720;
        var height = Number(container.dataset.height || 220);
        var padding = { top: 12, right: 12, bottom: 26, left: 52 };
        var plotWidth = width - padding.left - padding.right;
        var plotHeight = height - padding.top - padding.bottom;

        var values = config.series[0].values;
        var maxValue = Math.max.apply(null, values.concat([0])) || 1;
        var count = values.length;
        var slot = plotWidth / count;
        var barWidth = Math.max(3, Math.min(slot * 0.65, 46));

        var chart = svg("svg", {
            class: "chart",
            viewBox: "0 0 " + width + " " + height,
            role: "img",
            "aria-label": config.title || "Диаграмма"
        });

        var grid = svg("g", { class: "chart__grid" });
        for (var step = 0; step <= 4; step++) {
            var value = (maxValue * step) / 4;
            var lineY = padding.top + plotHeight - (value / maxValue) * plotHeight;
            grid.appendChild(svg("line", {
                x1: padding.left, y1: lineY, x2: width - padding.right, y2: lineY
            }));
            var label = svg("text", {
                class: "chart__axis", x: padding.left - 8, y: lineY + 3, "text-anchor": "end"
            });
            label.textContent = formatTick(value);
            grid.appendChild(label);
        }
        chart.appendChild(grid);

        values.forEach(function (value, index) {
            var barHeight = Math.max(1, (value / maxValue) * plotHeight);
            var bar = svg("rect", {
                class: "chart__bar" + (config.tones && config.tones[index]
                    ? " chart__bar--" + config.tones[index] : ""),
                x: padding.left + slot * index + (slot - barWidth) / 2,
                y: padding.top + plotHeight - barHeight,
                width: barWidth,
                height: barHeight,
                rx: 2
            });
            var title = svg("title");
            title.textContent = config.labels[index] + ": " + formatTick(value);
            bar.appendChild(title);
            chart.appendChild(bar);
        });

        var stride = Math.max(1, Math.ceil(count / 8));
        for (var i = 0; i < count; i += stride) {
            var tick = svg("text", {
                class: "chart__axis",
                x: padding.left + slot * i + slot / 2,
                y: height - 8,
                "text-anchor": "middle"
            });
            tick.textContent = config.labels[i];
            chart.appendChild(tick);
        }

        container.innerHTML = "";
        container.appendChild(chart);
    }

    /** Построить все графики, объявленные на странице. */
    function renderCharts() {
        document.querySelectorAll("[data-chart]").forEach(function (container) {
            var config;
            try {
                config = JSON.parse(container.dataset.chart);
            } catch (error) {
                return;
            }
            if (!config.series || !config.series.length) return;
            if ((container.dataset.chartType || "line") === "bar") {
                renderBar(container, config);
            } else {
                renderLine(container, config);
            }
        });
    }

    renderCharts();

    /* Перестроение при изменении ширины окна и при смене темы: размеры и
       цвета графиков зависят и от того, и от другого. */
    var resizeTimer = null;
    window.addEventListener("resize", function () {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(renderCharts, 200);
    });

    window.FreightFlow = {
        renderCharts: renderCharts,
        applyTheme: applyTheme,
        formatTick: formatTick
    };
})();
