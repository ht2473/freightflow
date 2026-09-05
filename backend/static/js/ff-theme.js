/* Установка оформления до первой отрисовки страницы.

   Сценарий подключается в <head> без признаков defer и async: он обязан
   выполниться раньше, чем браузер нарисует первый кадр. Основной сценарий
   страницы загружается отложенно и к этому моменту ещё не разобран —
   без такой вставки страница на мгновение показывалась бы в оформлении
   по умолчанию, а затем перекрашивалась.

   Отдельным файлом, а не внутри разметки: страница объявляет политику
   безопасности содержимого, по которой исполняются только сценарии,
   пришедшие со своего домена. */
(function () {
    var root = document.documentElement;
    var choice;
    try {
        choice = localStorage.getItem("ff-theme");
    } catch (error) {
        choice = null;
    }
    if (choice !== "dark" && choice !== "light" && choice !== "auto") {
        choice = root.dataset.themeDefault || "light";
    }
    var applied = choice;
    if (choice === "auto") {
        applied = window.matchMedia
            && window.matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark" : "light";
    }
    root.setAttribute("data-theme", applied);
    root.setAttribute("data-theme-choice", choice);
})();
