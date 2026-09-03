"""Клиент Overpass API с кешированием ответов на диске.

Overpass — публичная служба с ограничением по нагрузке: запросы к ней идут
десятками секунд, а при частых обращениях сервер отвечает отказом. Поэтому
каждый ответ сохраняется на диск и повторный запрос с теми же параметрами
обслуживается из кеша.

Кеш решает три задачи сразу:

* повторная загрузка данных не создаёт нагрузки на общедоступную службу;
* набор проверок исполняется без обращения к сети;
* результат загрузки воспроизводим — по сохранённому ответу видно, какие
  именно данные легли в базу и когда они были получены.

Ключ кеша — отпечаток текста запроса, поэтому правка запроса автоматически
приводит к новой загрузке.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("freightflow.etl")

#: Адрес службы по умолчанию. Зеркала взаимозаменяемы, но отличаются
#: доступностью и ограничениями, поэтому вынесены в настройку.
DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"

#: Служба отвечает ``406 Not Acceptable`` на запрос без User-Agent.
#: Значение записано латиницей: заголовки HTTP кодируются latin-1, и кириллица
#: в них приводит к отказу на стороне клиента ещё до отправки запроса.
USER_AGENT = "FreightFlow/1.0 (logistics information system; academic project)"

#: Пауза между повторами при временном отказе, секунды. Overpass отвечает
#: кодом 429 при превышении квоты и 504 при перегрузке; в обоих случаях
#: помогает выждать, а не повторять немедленно.
RETRY_DELAYS = (5, 20, 60)


class OverpassError(RuntimeError):
    """Ошибка обращения к Overpass API."""


@dataclass(frozen=True)
class OverpassResponse:
    """Ответ службы вместе со сведениями о его происхождении."""

    elements: list[dict]
    fetched_at: datetime
    from_cache: bool
    query_digest: str

    @property
    def count(self) -> int:
        return len(self.elements)


class OverpassClient:
    """Обращение к Overpass API с кешированием ответов.

    Аргументы:
        cache_dir: каталог хранения ответов; по умолчанию ``FF_OSM_CACHE_DIR``;
        endpoint: адрес службы;
        offline: работать только по кешу и отказывать при его отсутствии.
            Режим нужен там, где обращение в сеть недопустимо, — прежде всего
            в наборе проверок.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        endpoint: str | None = None,
        offline: bool = False,
        timeout: int = 300,
    ):
        self.cache_dir = Path(cache_dir or settings.OSM_CACHE_DIR)
        self.endpoint = endpoint or settings.OVERPASS_ENDPOINT
        self.offline = offline
        self.timeout = timeout
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ кеш

    @staticmethod
    def digest(query: str) -> str:
        """Отпечаток запроса, служащий ключом кеша.

        Считается по тексту, приведённому к единому виду: незначащие пробелы
        и переводы строк не должны обесценивать кеш при переформатировании
        запроса.
        """
        normalized = " ".join(query.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def cache_path(self, query: str) -> Path:
        return self.cache_dir / f"overpass-{self.digest(query)}.json.gz"

    def _read_cache(self, path: Path) -> tuple[list[dict], datetime] | None:
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            return payload["elements"], fetched_at
        except (OSError, ValueError, KeyError) as exc:
            # Повреждённый файл кеша не должен останавливать загрузку:
            # он равносилен его отсутствию.
            logger.warning("Кеш %s не прочитан (%s), будет получен заново", path.name, exc)
            return None

    def _write_cache(self, path: Path, elements: list[dict], fetched_at: datetime) -> None:
        payload = {
            "fetched_at": fetched_at.isoformat(),
            "endpoint": self.endpoint,
            "elements": elements,
        }
        # Запись во временный файл с последующим переименованием: прерванная
        # загрузка не оставит наполовину записанный кеш, который выглядел бы
        # исправным.
        temporary = path.with_suffix(".part")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        temporary.replace(path)

    # -------------------------------------------------------------- запросы

    def fetch(self, query: str, refresh: bool = False) -> OverpassResponse:
        """Выполнить запрос и вернуть элементы ответа.

        Аргументы:
            query: текст запроса на языке Overpass QL;
            refresh: не использовать кеш, обратиться к службе заново.
        """
        path = self.cache_path(query)

        if not refresh:
            cached = self._read_cache(path)
            if cached is not None:
                elements, fetched_at = cached
                logger.info(
                    "Overpass: из кеша %s, элементов %d, получено %s",
                    path.name, len(elements), fetched_at.date(),
                )
                return OverpassResponse(elements, fetched_at, True, self.digest(query))

        if self.offline:
            raise OverpassError(
                f"Нет кеша для запроса {self.digest(query)} и запрещено обращение "
                f"в сеть. Выполните загрузку без режима offline либо поместите "
                f"ответ в {path}."
            )

        elements = self._request(query)
        fetched_at = datetime.now(UTC)
        self._write_cache(path, elements, fetched_at)
        logger.info("Overpass: получено элементов %d, сохранено в %s", len(elements), path.name)
        return OverpassResponse(elements, fetched_at, False, self.digest(query))

    def _request(self, query: str) -> list[dict]:
        """Обратиться к службе, повторяя попытку при временном отказе."""
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

        last_error: Exception | None = None
        for attempt, delay in enumerate((0, *RETRY_DELAYS)):
            if delay:
                logger.warning("Overpass: повтор через %d с (попытка %d)", delay, attempt)
                time.sleep(delay)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                return payload.get("elements", [])
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 429 — превышена квота, 504 — перегрузка. Оба состояния
                # временные; остальные коды повторять бессмысленно.
                if exc.code not in (429, 502, 503, 504):
                    raise OverpassError(f"Overpass ответил {exc.code}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = exc

        raise OverpassError(f"Overpass недоступен после повторов: {last_error}")


def moscow_area(admin_level: int = 4) -> str:
    """Объявление области поиска — города Москвы.

    Уровень 4 соответствует субъекту федерации. Границы включают
    присоединённые в 2012 году территории, что для задач грузовой логистики
    существенно: крупнейшие складские комплексы расположены именно там.
    """
    return f'area["name"="Москва"]["admin_level"="{admin_level}"]->.searchArea;'
