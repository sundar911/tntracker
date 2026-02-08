from __future__ import annotations

from django.db import models
from django.db.models import Q


class SourceDocument(models.Model):
    class SourceType(models.TextChoices):
        OFFICIAL = "official", "Official"
        ADR = "adr", "ADR/MyNeta"
        MEDIA = "media", "Media"

    title = models.CharField(max_length=255)
    url = models.URLField(max_length=1000, blank=True)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    published_at = models.DateField(null=True, blank=True)
    retrieved_at = models.DateTimeField(auto_now_add=True)
    checksum = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.title} ({self.source_type})"


class Election(models.Model):
    year = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    data_vintage_label = models.CharField(max_length=255, blank=True)
    source_document = models.ForeignKey(SourceDocument, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-year"]

    def __str__(self) -> str:
        return f"{self.name} ({self.year})"


class Constituency(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_ta = models.CharField(max_length=255, blank=True)
    number = models.PositiveIntegerField(null=True, blank=True)
    district = models.CharField(max_length=255, blank=True)
    district_ta = models.CharField(max_length=255, blank=True)
    reservation_category = models.CharField(max_length=50, blank=True)
    reservation_category_ta = models.CharField(max_length=50, blank=True)
    boundary_geojson = models.JSONField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Party(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_ta = models.CharField(max_length=255, blank=True)
    abbreviation = models.CharField(max_length=50, blank=True)
    abbreviation_ta = models.CharField(max_length=50, blank=True)
    symbol_url = models.URLField(max_length=500, blank=True)
    website = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.abbreviation or self.name


class Coalition(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_ta = models.CharField(max_length=255, blank=True)
    election = models.ForeignKey(
        "Election",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coalitions",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CoalitionMembership(models.Model):
    coalition = models.ForeignKey(Coalition, on_delete=models.CASCADE, related_name="memberships")
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="coalition_memberships")

    class Meta:
        unique_together = ("coalition", "party")
        ordering = ["coalition__name", "party__name"]

    def __str__(self) -> str:
        return f"{self.party} in {self.coalition}"


class Candidate(models.Model):
    class Status(models.TextChoices):
        APPLIED = "applied", "Applied"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        CONTESTING = "contesting", "Contesting"
        ANNOUNCED = "announced", "Announced"

    name = models.CharField(max_length=255)
    name_ta = models.CharField(max_length=255, blank=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL)
    constituency = models.ForeignKey(Constituency, on_delete=models.CASCADE, related_name="candidates")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    age = models.PositiveIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=50, blank=True)
    education = models.CharField(max_length=255, blank=True)
    education_ta = models.CharField(max_length=255, blank=True)
    profession = models.CharField(max_length=255, blank=True)
    profession_ta = models.CharField(max_length=255, blank=True)
    address = models.TextField(blank=True)
    address_ta = models.TextField(blank=True)
    photo_url = models.URLField(max_length=500, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("name", "constituency")

    def __str__(self) -> str:
        return f"{self.name} ({self.constituency.name})"


class CandidateResult(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="results")
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="results")
    votes = models.PositiveIntegerField(null=True, blank=True)
    vote_share = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    position = models.PositiveIntegerField(null=True, blank=True)
    is_winner = models.BooleanField(default=False)

    class Meta:
        unique_together = ("candidate", "election")

    def __str__(self) -> str:
        return f"{self.candidate.name} - {self.election.year}"


class Affidavit(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="affidavits")
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    criminal_cases_count = models.PositiveIntegerField(null=True, blank=True)
    serious_criminal_cases_count = models.PositiveIntegerField(null=True, blank=True)
    assets_total = models.BigIntegerField(null=True, blank=True)
    liabilities_total = models.BigIntegerField(null=True, blank=True)
    education = models.CharField(max_length=255, blank=True)
    education_ta = models.CharField(max_length=255, blank=True)
    additional_details = models.JSONField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Affidavit: {self.candidate.name}"


class LegalCase(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="legal_cases")
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    case_number = models.CharField(max_length=255, blank=True)
    court = models.CharField(max_length=255, blank=True)
    court_ta = models.CharField(max_length=255, blank=True)
    sections = models.CharField(max_length=255, blank=True)
    sections_ta = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=255, blank=True)
    status_ta = models.CharField(max_length=255, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    description_ta = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"Case: {self.candidate.name}"


class Manifesto(models.Model):
    party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="manifestos"
    )
    coalition = models.ForeignKey(
        Coalition,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="manifestos",
    )
    constituency = models.ForeignKey(
        Constituency, null=True, blank=True, on_delete=models.SET_NULL, related_name="manifestos"
    )
    candidate = models.ForeignKey(
        Candidate, null=True, blank=True, on_delete=models.SET_NULL, related_name="manifestos"
    )
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    summary = models.TextField(blank=True)
    summary_ta = models.TextField(blank=True)
    document_url = models.URLField(max_length=1000, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        scope = self.constituency.name if self.constituency else "Statewide"
        owner = self.party or self.coalition or "Independent"
        return f"Manifesto: {owner} ({scope})"


class ManifestoDocument(models.Model):
    class Language(models.TextChoices):
        EN = "en", "English"
        TA = "ta", "Tamil"

    manifesto = models.ForeignKey(Manifesto, on_delete=models.CASCADE, related_name="documents")
    language = models.CharField(max_length=2, choices=Language.choices)
    url = models.URLField(max_length=1000)
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    checksum = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("manifesto", "language")
        ordering = ["manifesto_id", "language"]

    def __str__(self) -> str:
        return f"{self.manifesto} ({self.language})"


class ManifestoPromise(models.Model):
    manifesto = models.ForeignKey(Manifesto, on_delete=models.CASCADE, related_name="promises")
    slug = models.SlugField(max_length=255)
    text = models.TextField(blank=True)
    text_ta = models.TextField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    position = models.PositiveIntegerField(null=True, blank=True)
    tags = models.JSONField(null=True, blank=True)
    is_key = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("manifesto", "slug")
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["manifesto", "is_key"])]

    def __str__(self) -> str:
        label = (self.text or self.text_ta or self.slug or "").strip()
        label = label[:80] + ("…" if len(label) > 80 else "")
        return f"{self.manifesto}: {label}"


class PromiseAssessment(models.Model):
    class Scope(models.TextChoices):
        STATE = "state", "State"
        CONSTITUENCY = "constituency", "Constituency"

    class Status(models.TextChoices):
        FULFILLED = "fulfilled", "Fulfilled"
        PARTIAL = "partial", "Partially fulfilled"
        NOT_FULFILLED = "not_fulfilled", "Not fulfilled"
        UNKNOWN = "unknown", "Unknown/Unverified"
        DISPUTED = "disputed", "Disputed"

    promise = models.ForeignKey(ManifestoPromise, on_delete=models.CASCADE, related_name="assessments")
    scope = models.CharField(max_length=20, choices=Scope.choices)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL)
    constituency = models.ForeignKey(Constituency, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    score = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    summary = models.TextField(blank=True)
    summary_ta = models.TextField(blank=True)
    as_of = models.DateField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-as_of", "id"]
        constraints = [
            models.CheckConstraint(
                name="promise_assessment_scope_party_or_constituency",
                check=(
                    (Q(scope="state") & Q(party__isnull=False) & Q(constituency__isnull=True))
                    | (Q(scope="constituency") & Q(constituency__isnull=False))
                    | Q(scope__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="promise_assessment_score_between_0_and_1",
                check=Q(score__isnull=True) | (Q(score__gte=0) & Q(score__lte=1)),
            ),
        ]

    def __str__(self) -> str:
        target = self.party or self.constituency or "Unknown"
        return f"{self.promise.slug} ({self.scope} - {target}): {self.status}"


class PromiseEvidence(models.Model):
    assessment = models.ForeignKey(PromiseAssessment, on_delete=models.CASCADE, related_name="evidence")
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    url = models.URLField(max_length=1000, blank=True)
    quote = models.TextField(blank=True)
    published_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self) -> str:
        return f"Evidence: {self.source_document}"


class PartyFulfilmentClaim(models.Model):
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="fulfilment_claims")
    election = models.ForeignKey(Election, null=True, blank=True, on_delete=models.SET_NULL)
    claimed_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    claimed_by = models.CharField(max_length=255, blank=True)
    as_of = models.DateField(null=True, blank=True)
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    snippet = models.TextField(blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-as_of", "-id"]

    def __str__(self) -> str:
        pct = f"{self.claimed_percent}%" if self.claimed_percent is not None else "N/A"
        return f"Claim: {self.party} {pct}"


class UpdateLog(models.Model):
    entity_type = models.CharField(max_length=100)
    entity_id = models.PositiveIntegerField()
    source_document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.entity_type} {self.entity_id} update"


class Feedback(models.Model):
    ease_of_use = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-5
    helps_inform = models.CharField(max_length=20, blank=True)  # yes / somewhat / no
    would_return = models.CharField(max_length=20, blank=True)  # definitely / probably / not_sure / no
    suggestion = models.TextField(blank=True)
    page_url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Feedback #{self.pk} ({self.created_at:%Y-%m-%d %H:%M})"
