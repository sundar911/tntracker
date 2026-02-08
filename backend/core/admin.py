from django.contrib import admin

from .models import (
    Affidavit,
    Candidate,
    CandidateResult,
    Coalition,
    CoalitionMembership,
    Constituency,
    Election,
    Feedback,
    LegalCase,
    Manifesto,
    ManifestoDocument,
    ManifestoPromise,
    Party,
    PartyFulfilmentClaim,
    PromiseAssessment,
    PromiseEvidence,
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
    list_display = ("party", "coalition", "constituency", "candidate", "last_updated")
    search_fields = ("party__name", "coalition__name", "constituency__name", "candidate__name")


@admin.register(ManifestoDocument)
class ManifestoDocumentAdmin(admin.ModelAdmin):
    list_display = ("manifesto", "language", "url", "last_updated")
    search_fields = ("manifesto__party__name", "manifesto__coalition__name", "url")
    list_filter = ("language",)


@admin.register(ManifestoPromise)
class ManifestoPromiseAdmin(admin.ModelAdmin):
    list_display = ("manifesto", "slug", "category", "is_key", "position", "last_updated")
    search_fields = ("slug", "text", "text_ta", "manifesto__party__name", "manifesto__coalition__name")
    list_filter = ("is_key", "category")


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "source_type", "published_at", "retrieved_at")
    search_fields = ("title", "url")


@admin.register(UpdateLog)
class UpdateLogAdmin(admin.ModelAdmin):
    list_display = ("entity_type", "entity_id", "source_document", "created_at")


@admin.register(Coalition)
class CoalitionAdmin(admin.ModelAdmin):
    list_display = ("name", "election")
    search_fields = ("name",)
    list_filter = ("election",)


@admin.register(CoalitionMembership)
class CoalitionMembershipAdmin(admin.ModelAdmin):
    list_display = ("coalition", "party")
    search_fields = ("coalition__name", "party__name")
    list_filter = ("coalition",)


@admin.register(PromiseAssessment)
class PromiseAssessmentAdmin(admin.ModelAdmin):
    list_display = ("promise", "scope", "party", "constituency", "status", "score", "as_of", "last_updated")
    list_filter = ("scope", "status", "as_of")
    search_fields = ("promise__slug", "promise__text", "promise__text_ta", "summary", "summary_ta")


@admin.register(PromiseEvidence)
class PromiseEvidenceAdmin(admin.ModelAdmin):
    list_display = ("assessment", "source_document", "published_at", "created_at")
    search_fields = ("source_document__title", "source_document__url", "quote", "url")
    list_filter = ("published_at",)


@admin.register(PartyFulfilmentClaim)
class PartyFulfilmentClaimAdmin(admin.ModelAdmin):
    list_display = ("party", "election", "claimed_percent", "claimed_by", "as_of", "last_updated")
    list_filter = ("election", "as_of")
    search_fields = ("party__name", "claimed_by", "snippet", "source_document__title")


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("pk", "ease_of_use", "helps_inform", "would_return", "page_url", "created_at")
    list_filter = ("ease_of_use", "helps_inform", "would_return")
    readonly_fields = ("ease_of_use", "helps_inform", "would_return", "suggestion", "page_url", "created_at")
