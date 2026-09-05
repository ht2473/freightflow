"""Постраничная выдача REST API."""

from __future__ import annotations

from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """Единый формат постраничной выдачи для всех конечных точек API.

    Размер страницы регулируется параметром ``page_size`` и ограничен сверху,
    чтобы одиночный запрос не мог выгрузить всю базу целиком.
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500

    def get_paginated_response(self, data) -> Response:
        """Дополнить ответ сведениями о странице и общем числе записей."""
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("pages", self.page.paginator.num_pages),
                    ("page", self.page.number),
                    ("page_size", self.get_page_size(self.request)),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )
