from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("api/constituencies", views.ConstituencyViewSet, basename="constituency")
router.register("api/parties", views.PartyViewSet, basename="party")
router.register("api/candidates", views.CandidateViewSet, basename="candidate")
router.register("api/manifestos", views.ManifestoViewSet, basename="manifesto")

urlpatterns = [
    path("", views.home, name="home"),
    path("map/", views.map_view, name="map"),
    path("resources/", views.resources, name="resources"),
    path("map/data/", views.map_data, name="map-data"),
    path("api/map-search/", views.map_search, name="map-search"),
    path("search/", views.search, name="search"),
    path("dashboard/", views.data_quality_dashboard, name="dashboard"),
    path("party-dashboard/", views.party_dashboard, name="party-dashboard"),
    path("party/<path:party_name>/", views.party_detail, name="party-detail"),
    path("set-lang/<str:language>/", views.set_language, name="set-language"),
    path("constituency/<int:constituency_id>/", views.constituency_detail, name="constituency-detail"),
    path("candidate/<int:candidate_id>/", views.candidate_detail, name="candidate-detail"),
    path("feedback/", views.submit_feedback, name="feedback"),
    path("", include(router.urls)),
]
