from __future__ import annotations

from django.db import models


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
        return f"Manifesto: {self.party or 'Independent'} ({scope})"


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
