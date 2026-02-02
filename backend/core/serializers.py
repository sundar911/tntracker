from rest_framework import serializers

from .models import Candidate, Constituency, Manifesto, Party


class PartySerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = ("id", "name", "name_ta", "abbreviation", "abbreviation_ta", "symbol_url", "website")


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
