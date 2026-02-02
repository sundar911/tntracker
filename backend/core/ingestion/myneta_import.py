from __future__ import annotations

from core.ingestion.myneta import MynetaProfile
from core.models import Affidavit, Candidate, Constituency, LegalCase, Party, SourceDocument


def upsert_myneta_profile(profile: MynetaProfile, source_url: str) -> int:
    source = SourceDocument.objects.create(
        title=f"MyNeta: {profile.name}",
        url=source_url,
        source_type=SourceDocument.SourceType.ADR,
    )

    constituency, _ = Constituency.objects.get_or_create(name=profile.constituency)
    party, _ = Party.objects.get_or_create(name=profile.party or "Independent")
    candidate, _ = Candidate.objects.get_or_create(
        name=profile.name,
        constituency=constituency,
        defaults={"party": party, "status": Candidate.Status.CONTESTING},
    )
    candidate.party = party
    candidate.save()

    Affidavit.objects.update_or_create(
        candidate=candidate,
        source_document=source,
        defaults={
            "criminal_cases_count": profile.criminal_cases,
            "serious_criminal_cases_count": profile.serious_cases,
        },
    )

    created_cases = 0
    for case in profile.cases:
        LegalCase.objects.get_or_create(
            candidate=candidate,
            source_document=source,
            case_number=case.case_number,
            defaults={
                "sections": case.sections,
                "status": case.status,
                "court": case.court,
                "year": case.year,
                "description": case.description,
            },
        )
        created_cases += 1

    return created_cases
