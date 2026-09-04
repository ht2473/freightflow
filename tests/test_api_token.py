"""Тесты программного доступа по персональному токену.

Метод: тестирование безопасности — проверяется, что предъявленный токен
опознаёт пользователя, что негодный токен отвергается, а разграничение
методов опирается на роль владельца, а не на факт наличия заголовка.
"""

from __future__ import annotations

import pytest
from accounts.models import Role, UserProfile, profile_by_api_token

pytestmark = pytest.mark.django_db

BASE = "/api/v1/"


def token_for(user) -> str:
    """Выпустить токен для учётной записи и вернуть его значение."""
    return UserProfile.objects.get(user=user).issue_api_token()


def header(token: str) -> dict:
    """Заголовок авторизации с указанным токеном."""
    return {"HTTP_AUTHORIZATION": f"Token {token}"}


class TestTokenStorage:
    """Хранение токена."""

    def test_value_is_not_stored(self, users):
        """В базу попадает отпечаток, а не сам токен."""
        raw = token_for(users[Role.ANALYST])
        profile = UserProfile.objects.get(user=users[Role.ANALYST])
        assert raw not in (profile.api_token_hash, profile.api_token_prefix)
        assert len(profile.api_token_hash) == 64

    def test_prefix_identifies_token(self, users):
        """Сохранённое начало значения совпадает с началом выданного токена."""
        raw = token_for(users[Role.ANALYST])
        profile = UserProfile.objects.get(user=users[Role.ANALYST])
        assert raw.startswith(profile.api_token_prefix)

    def test_lookup_finds_owner(self, users):
        """По значению токена отыскивается его владелец."""
        raw = token_for(users[Role.ANALYST])
        assert profile_by_api_token(raw).user == users[Role.ANALYST]

    def test_issue_revokes_previous(self, users):
        """Выпуск нового токена делает прежний непригодным."""
        old = token_for(users[Role.ANALYST])
        token_for(users[Role.ANALYST])
        assert profile_by_api_token(old) is None

    def test_revoke_clears_token(self, users):
        """Отзыв токена прекращает его действие."""
        raw = token_for(users[Role.ANALYST])
        profile = UserProfile.objects.get(user=users[Role.ANALYST])
        profile.revoke_api_token()
        assert profile_by_api_token(raw) is None
        assert not profile.has_api_token


class TestAuthentication:
    """Опознание пользователя по токену."""

    def test_token_identifies_user(self, client, users):
        """Метод сведений о владельце называет учётную запись и роль."""
        raw = token_for(users[Role.ANALYST])
        payload = client.get(f"{BASE}me/", **header(raw)).json()
        assert payload["username"] == users[Role.ANALYST].username
        assert payload["role"] == Role.ANALYST
        assert payload["permissions"]["export"] is True

    def test_request_without_token_is_anonymous(self, client, db):
        """Обращение без заголовка к методу с авторизацией отвергается."""
        assert client.get(f"{BASE}me/").status_code in (401, 403)

    def test_invalid_token_rejected(self, client, db):
        """Неизвестный токен отвергается, а не обслуживается как анонимный."""
        assert client.get(f"{BASE}me/", **header("ff_unknown")).status_code == 401

    def test_malformed_header_rejected(self, client, users):
        """Заголовок без значения токена признаётся ошибочным."""
        response = client.get(f"{BASE}me/", HTTP_AUTHORIZATION="Token")
        assert response.status_code == 401

    def test_foreign_scheme_ignored(self, client, db):
        """Заголовок с чужой схемой не мешает открытым методам."""
        response = client.get(f"{BASE}districts/", HTTP_AUTHORIZATION="Basic cXdl")
        assert response.status_code == 200

    def test_inactive_account_rejected(self, client, users):
        """Отключение учётной записи прекращает действие её токена."""
        raw = token_for(users[Role.ANALYST])
        users[Role.ANALYST].is_active = False
        users[Role.ANALYST].save(update_fields=["is_active"])
        assert client.get(f"{BASE}me/", **header(raw)).status_code == 401

    def test_use_is_recorded(self, client, users):
        """Обращение по токену отмечается временем последнего использования."""
        raw = token_for(users[Role.ANALYST])
        client.get(f"{BASE}me/", **header(raw))
        assert UserProfile.objects.get(user=users[Role.ANALYST]).api_token_used is not None


class TestExportEndpoint:
    """Выгрузка отчётов по токену."""

    def test_requires_token(self, client, full_dataset):
        """Без авторизации отчёт не формируется."""
        assert client.get(f"{BASE}exports/objects.xlsx").status_code in (401, 403)

    def test_role_below_analyst_rejected(self, client, full_dataset, users):
        """Наблюдателю выгрузка недоступна: роль проверяется, а не токен."""
        raw = token_for(users[Role.VIEWER])
        assert client.get(f"{BASE}exports/objects.xlsx", **header(raw)).status_code == 403

    def test_analyst_receives_file(self, client, full_dataset, users):
        """Аналитик получает файл отчёта с указанием числа записей."""
        raw = token_for(users[Role.ANALYST])
        response = client.get(f"{BASE}exports/objects.csv", **header(raw))
        assert response.status_code == 200
        assert int(response["X-Export-Rows"]) > 0
        assert response["Content-Type"].startswith("text/csv")

    def test_filters_narrow_selection(self, client, full_dataset, users, districts):
        """Условия отбора действуют теми же параметрами, что и на страницах."""
        raw = token_for(users[Role.ANALYST])
        whole = client.get(f"{BASE}exports/objects.csv", **header(raw))
        narrowed = client.get(
            f"{BASE}exports/objects.csv?district={districts[0].pk}", **header(raw)
        )
        assert int(narrowed["X-Export-Rows"]) < int(whole["X-Export-Rows"])

    def test_unknown_dataset_rejected(self, client, full_dataset, users):
        """Несуществующий набор данных отвергается с пояснением."""
        raw = token_for(users[Role.ANALYST])
        response = client.get(f"{BASE}exports/nonesuch.csv", **header(raw))
        assert response.status_code == 400
        assert "набор" in response.json()["detail"].lower()

    def test_geojson_refused_for_flat_dataset(self, client, full_dataset, users):
        """Набор без геометрии в пространственный слой не выгружается."""
        raw = token_for(users[Role.ANALYST])
        response = client.get(f"{BASE}exports/flows.geojson", **header(raw))
        assert response.status_code == 400

    def test_export_lands_in_cabinet(self, client, full_dataset, users):
        """Отчёт, заказанный по токену, попадает в центр выгрузок."""
        from accounts.models import ExportJob

        raw = token_for(users[Role.ANALYST])
        client.get(f"{BASE}exports/objects.csv", **header(raw))
        assert ExportJob.objects.filter(user=users[Role.ANALYST], status="done").exists()
