from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from exams.views import dashboard

admin.site.site_header = "Адал — администрирование"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("accounts/", include("accounts.urls")),
    path("", include("exams.urls")),
    path("admin/", admin.site.urls),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
