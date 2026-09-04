"""Реестр конвейеров загрузки.

Состав источников системы читается отсюда целиком: командная строка, панель
администратора и регламентные задания обращаются к одному и тому же реестру
и потому не могут разойтись в том, какие наборы данных существуют.

Порядок объявления значим. Наборы загружаются в нём при запуске без
ограничений, а зависимости между ними односторонние: объект относится
к округу по координатам, зона строится по геометрии кольцевой магистрали,
принадлежность магистрали грузовому каркасу отмечается в уже заполненном
реестре.
"""

from __future__ import annotations

from .pipeline import Pipeline

#: Конвейеры в порядке загрузки. Заполняется при первом обращении к реестру.
_PIPELINES: dict[str, Pipeline] = {}


def _load() -> dict[str, Pipeline]:
    """Собрать реестр конвейеров.

    Модули загружаются здесь, а не при импорте реестра: конвейеры обращаются
    к моделям, и импорт на уровне модуля потребовал бы готового приложения
    в момент разбора файла.
    """
    if _PIPELINES:
        return _PIPELINES

    from .osm.corridors import CorridorsPipeline
    from .osm.incidents import RoadworksPipeline
    from .osm.loaders import DistrictsPipeline, InfrastructurePipeline
    from .osm.roads import RoadNetworkPipeline
    from .osm.zones import RestrictionZonesPipeline
    from .reference import FreightFramePipeline
    from .rosstat import FreightStatisticsPipeline
    from .upload import FlowUploadPipeline

    ordered: tuple[type[Pipeline], ...] = (
        DistrictsPipeline,
        InfrastructurePipeline,
        RoadNetworkPipeline,
        RestrictionZonesPipeline,
        CorridorsPipeline,
        RoadworksPipeline,
        FreightFramePipeline,
        FreightStatisticsPipeline,
        FlowUploadPipeline,
    )
    for pipeline_class in ordered:
        pipeline = pipeline_class()
        _PIPELINES[pipeline.name] = pipeline
    return _PIPELINES


def available() -> list[Pipeline]:
    """Все конвейеры в порядке загрузки."""
    return list(_load().values())


def names() -> list[str]:
    """Обозначения конвейеров — для ключей командной строки."""
    return list(_load())


def get(name: str) -> Pipeline:
    """Конвейер по обозначению."""
    try:
        return _load()[name]
    except KeyError as exc:
        known = ", ".join(names())
        raise KeyError(f"Конвейер «{name}» неизвестен. Объявлены: {known}") from exc


def scheduled() -> list[Pipeline]:
    """Конвейеры, для которых объявлена регламентная периодичность."""
    return [pipeline for pipeline in available() if pipeline.frequency]


__all__ = ["available", "get", "names", "scheduled"]
