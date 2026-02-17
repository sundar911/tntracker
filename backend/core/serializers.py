from rest_framework import serializers

from .models import Candidate, Constituency, Manifesto, Party


class PartySerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = (
            "id", "name", "name_ta", "abbreviation", "abbreviation_ta",
            "symbol_url", "website",
            "founded_year", "founder", "current_leader", "headquarters",
            "political_ideology", "political_position", "eci_recognition",
            "governance_record_note",
        )


class ConstituencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Constituency
        fields = (
            "id",
            "name",
            "name_ta",
            "number",
            "district",
            "district_ta",
            "reservation_category",
            "reservation_category_ta",
            "boundary_geojson",
            "parliamentary_constituency",
            "region",
            "urbanization_type",
            "population",
            "area_sq_km",
            "infant_mortality_rate",
            "under5_mortality_rate",
            "institutional_delivery_pct",
            "child_stunting_pct",
            "child_wasting_pct",
            "full_immunization_pct",
            "anaemia_women_pct",
            "literacy_rate_pct",
            "male_literacy_rate_pct",
            "female_literacy_rate_pct",
            "literacy_gender_gap_pct",
            "secondary_education_pct",
            "graduate_and_above_pct",
            "per_capita_income_inr",
            "bpl_households_pct",
            "unemployment_rate_pct",
            "agricultural_workers_pct",
            "banking_access_pct",
            "crime_rate_per_lakh",
            "crimes_against_women_per_lakh",
            "crimes_against_sc_st_per_lakh",
            "pucca_housing_pct",
            "tap_water_pct",
            "electricity_pct",
            "sanitation_pct",
        )


class CandidateSerializer(serializers.ModelSerializer):
    party = PartySerializer()
    constituency = ConstituencySerializer()

    class Meta:
        model = Candidate
        fields = (
            "id",
            "name",
            "name_ta",
            "party",
            "constituency",
            "status",
            "age",
            "gender",
            "education",
            "education_ta",
            "profession",
            "profession_ta",
            "address",
            "address_ta",
            "photo_url",
            "last_updated",
        )


class ManifestoSerializer(serializers.ModelSerializer):
    party = PartySerializer()
    constituency = ConstituencySerializer()
    candidate = CandidateSerializer()

    class Meta:
        model = Manifesto
        fields = (
            "id",
            "party",
            "constituency",
            "candidate",
            "summary",
            "summary_ta",
            "document_url",
            "last_updated",
        )
