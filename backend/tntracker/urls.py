from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from core.sitemaps import CandidateSitemap, ConstituencySitemap, PartySitemap, StaticSitemap

sitemaps = {
    "static": StaticSitemap,
    "constituencies": ConstituencySitemap,
    "candidates": CandidateSitemap,
    "parties": PartySitemap,
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
