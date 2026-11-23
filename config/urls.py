from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, reverse

from exams.views import dashboard
from files.views import serve as serve_file

admin.site.site_header = "Администрирование Адал"


def robots_txt(request):
    """Поисковикам открыты только вход, регистрация и соглашение."""
    lines = [
        "User-agent: *",
        "Allow: /accounts/login/",
        "Allow: /accounts/register/",
        "Allow: /accounts/rules/",
        "Allow: /static/",
        "Disallow: /",
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


def sitemap_xml(request):
    base = f"{request.scheme}://{request.get_host()}"
    urls = "".join(
        f"<url><loc>{base}{reverse(name)}</loc></url>"
        for name in ("accounts:login", "accounts:register", "accounts:rules")
    )
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return HttpResponse(body, content_type="application/xml")


urlpatterns = [
    path("robots.txt", robots_txt),
    path("sitemap.xml", sitemap_xml),
    path("", dashboard, name="dashboard"),
    path("accounts/", include("accounts.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("exams.urls")),
    path("media/<path:name>", serve_file, name="file"),
    path("admin/", admin.site.urls),
]
