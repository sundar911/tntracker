from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Candidate, Constituency, Party


class StaticSitemap(Sitemap):
    priority = 1.0
    changefreq = "daily"

    def items(self):
        return ["home", "map", "party-dashboard", "resources", "search"]

    def location(self, item):
        return reverse(item)


class ConstituencySitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9

    def items(self):
        return Constituency.objects.all().order_by("id")

    def location(self, obj):
        return reverse("constituency-detail", args=[obj.pk])


class CandidateSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Candidate.objects.all().order_by("id")

    def location(self, obj):
        return reverse("candidate-detail", args=[obj.pk])


class PartySitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return list(Party.objects.exclude(name="").values_list("name", flat=True))

    def location(self, name):
        return reverse("party-detail", args=[name])
