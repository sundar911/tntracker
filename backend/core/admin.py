from django.contrib import admin

from .models import (
    Affidavit,
    Candidate,
    CandidateResult,
    Constituency,
    Election,
    LegalCase,
    Manifesto,
    Party,
    SourceDocument,
    UpdateLog,
)


@admin.register(Constituency)
class ConstituencyAdmin(admin.ModelAdmin):
    list_display = ("name", "number", "district", "reservation_category", "last_updated")
    search_fields = ("name", "district")


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "website")
    search_fields = ("name", "abbreviation")


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ("name", "party", "constituency", "status", "age")
    list_filter = ("status", "party")
    search_fields = ("name", "constituency__name", "party__name")


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):
    list_display = ("year", "name", "data_vintage_label")
    search_fields = ("name",)


@admin.register(CandidateResult)
class CandidateResultAdmin(admin.ModelAdmin):
    list_display = ("candidate", "election", "votes", "position", "is_winner")
    list_filter = ("election", "is_winner")


@admin.register(Affidavit)
class AffidavitAdmin(admin.ModelAdmin):
    list_display = (
        "candidate",
        "criminal_cases_count",
        "serious_criminal_cases_count",
        "assets_total",
        "liabilities_total",
        "last_updated",
    )
    search_fields = ("candidate__name",)


@admin.register(LegalCase)
class LegalCaseAdmin(admin.ModelAdmin):
    list_display = ("candidate", "case_number", "court", "status", "year")
    search_fields = ("candidate__name", "case_number", "court")


@admin.register(Manifesto)
class ManifestoAdmin(admin.ModelAdmin):
    list_display = ("party", "constituency", "candidate", "last_updated")
    search_fields = ("party__name", "constituency__name", "candidate__name")


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "source_type", "published_at", "retrieved_at")
    search_fields = ("title", "url")


@admin.register(UpdateLog)
class UpdateLogAdmin(admin.ModelAdmin):
    list_display = ("entity_type", "entity_id", "source_document", "created_at")
