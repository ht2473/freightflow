"""Представления модуля информационного наполнения."""

from __future__ import annotations

from core.views.base import page_context, paginate
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from .forms import FeedbackForm
from .models import Article, ArticleCategory, FeedbackMessage


def article_list(request):
    """Перечень аналитических материалов."""
    queryset = Article.objects.published().select_related("category")

    category_code = (request.GET.get("category") or "").strip()
    term = (request.GET.get("q") or "").strip()
    if category_code:
        queryset = queryset.filter(category__code=category_code)
    if term:
        queryset = queryset.filter(
            Q(title__icontains=term) | Q(lead__icontains=term) | Q(body__icontains=term)
        )

    categories = ArticleCategory.objects.annotate(
        total=Count("articles", filter=Q(articles__is_published=True))
    ).order_by("order")

    context = page_context(
        request,
        title=_("Аналитические материалы"),
        lead=_(
            "Обзоры и методические публикации о состоянии логистической "
            "инфраструктуры Москвы, подготовленные на данных системы."
        ),
        active="articles",
        crumbs=[(_("Материалы"),)],
        page_obj=paginate(request, queryset, per_page=8),
        categories=categories,
        featured=Article.objects.featured().first(),
        filters={"category": category_code, "q": term},
    )
    return render(request, "pages/article_list.html", context)


def article_detail(request, slug: str):
    """Страница аналитического материала."""
    article = get_object_or_404(
        Article.objects.published().select_related("category", "author"), slug=slug
    )
    article.register_view()

    related = (
        Article.objects.published()
        .filter(category=article.category)
        .exclude(pk=article.pk)[:3]
    )

    context = page_context(
        request,
        title=article.title,
        lead=article.lead,
        active="articles",
        crumbs=[
            (_("Материалы"), "content:article_list"),
            (article.category.name, "content:article_list"),
            (article.title,),
        ],
        article=article,
        related=related,
    )
    return render(request, "pages/article_detail.html", context)


def feedback(request):
    """Форма обратной связи."""
    if request.method == "POST":
        form = FeedbackForm(request.POST, request.FILES)
        if form.is_valid():
            message = form.save(commit=False)
            message.ip_address = _client_ip(request)
            message.user_agent = request.META.get("HTTP_USER_AGENT", "")[:300]
            message.save()
            messages.success(
                request,
                _("Обращение принято. Ответ будет направлен на указанный адрес "
                "электронной почты в течение трёх рабочих дней.",)
            )
            return redirect("content:feedback_sent")
    else:
        initial = {}
        if request.user.is_authenticated:
            initial = {
                "name": request.user.get_full_name() or request.user.username,
                "email": request.user.email,
            }
        form = FeedbackForm(initial=initial)

    context = page_context(
        request,
        title=_("Обратная связь"),
        lead=_(
            "Замечания к данным, предложения по развитию системы и сообщения "
            "об ошибках. Все обращения рассматриваются администратором системы."
        ),
        active="feedback",
        crumbs=[(_("Обратная связь"),)],
        form=form,
        topics=FeedbackMessage.Topic.choices,
        processed_count=FeedbackMessage.objects.filter(
            status=FeedbackMessage.Status.ANSWERED
        ).count(),
    )
    return render(request, "pages/feedback.html", context)


def feedback_sent(request):
    """Страница подтверждения отправки обращения."""
    context = page_context(
        request,
        title=_("Обращение отправлено"),
        lead=_("Спасибо за обратную связь."),
        active="feedback",
        crumbs=[(_("Обратная связь"), "content:feedback"), (_("Отправлено"),)],
    )
    return render(request, "pages/feedback_sent.html", context)


def _client_ip(request) -> str | None:
    """Определить адрес клиента с учётом обратного прокси."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
