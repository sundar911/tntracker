"""
Centralized Tamil translation dictionaries for static text values.

These cover values that come from CSV data or model fields
and need Tamil equivalents when the language toggle is set to Tamil.
"""

# Education level translations (from MyNeta affidavit data)
EDUCATION_TA = {
    "Post Graduate": "முதுகலை பட்டம்",
    "Graduate": "பட்டதாரி",
    "Graduate Professional": "பட்டதாரி தொழில்முறை",
    "12th Pass": "12-ஆம் வகுப்பு தேர்ச்சி",
    "10th Pass": "10-ஆம் வகுப்பு தேர்ச்சி",
    "8th Pass": "8-ஆம் வகுப்பு தேர்ச்சி",
    "5th Pass": "5-ஆம் வகுப்பு தேர்ச்சி",
    "Doctorate": "முனைவர் பட்டம்",
    "Diploma": "டிப்ளமா",
    "Literate": "எழுத்தறிவு",
    "Illiterate": "எழுத்தறிவின்மை",
    "Others": "பிற",
    "Not Given": "தரப்படவில்லை",
}

# Candidate status translations
STATUS_TA = {
    "contesting": "போட்டியிடுகிறார்",
    "announced": "அறிவிக்கப்பட்டது",
    "applied": "விண்ணப்பித்தார்",
    "accepted": "ஏற்றுக்கொள்ளப்பட்டது",
    "rejected": "நிராகரிக்கப்பட்டது",
    "withdrawn": "விலகினார்",
    "won": "வெற்றி",
    "lost": "தோல்வி",
    "Contesting": "போட்டியிடுகிறார்",
    "Announced": "அறிவிக்கப்பட்டது",
    "Applied": "விண்ணப்பித்தார்",
    "Accepted": "ஏற்றுக்கொள்ளப்பட்டது",
    "Rejected": "நிராகரிக்கப்பட்டது",
    "Withdrawn": "விலகினார்",
    "Won": "வெற்றி",
    "Lost": "தோல்வி",
}

# Gender translations
GENDER_TA = {
    "Male": "ஆண்",
    "Female": "பெண்",
    "Other": "பிற",
    "male": "ஆண்",
    "female": "பெண்",
    "other": "பிற",
}

# Resource page category translations
RESOURCE_CATEGORY_TA = {
    "Affidavits": "வாக்குமூலங்கள்",
    "Expenditure": "செலவு",
    "Economy": "பொருளாதாரம்",
    "Official": "அதிகாரப்பூர்வ",
    "Results": "முடிவுகள்",
    "Analytics": "பகுப்பாய்வு",
}

# Resource page title translations
RESOURCE_TITLE_TA = {
    "MyNeta - Tamil Nadu 2026 Candidate Affidavits": "MyNeta - தமிழ்நாடு 2026 வேட்பாளர் வாக்குமூலங்கள்",
    "MyNeta - Tamil Nadu Assembly Elections": "MyNeta - தமிழ்நாடு சட்டமன்றத் தேர்தல்கள்",
    "MyNeta - Asset Comparison of Re-contesting Winners": "MyNeta - மீண்டும் போட்டியிடும் வெற்றியாளர்களின் சொத்து ஒப்பீடு",
    "MLA Election Expenditure Analysis 2021 (ADR)": "சட்டமன்ற உறுப்பினர் தேர்தல் செலவு பகுப்பாய்வு 2021 (ADR)",
    "Tamil Nadu Economic Survey 2025-26": "தமிழ்நாடு பொருளாதார ஆய்வு 2025-26",
    "NITI Aayog - Tamil Nadu Fiscal Landscape": "நிதி ஆயோக் - தமிழ்நாடு நிதி நிலப்பரப்பு",
    "16th Tamil Nadu Legislative Assembly Members": "16-ஆவது தமிழ்நாடு சட்டமன்ற உறுப்பினர்கள்",
    "IndiaVotes - Tamil Nadu 2021 Results": "IndiaVotes - தமிழ்நாடு 2021 முடிவுகள்",
    "Tamil Nadu Assembly Elections Visual Analytics": "தமிழ்நாடு சட்டமன்றத் தேர்தல் காட்சி பகுப்பாய்வு",
    "PRS - Tamil Nadu Budget Analysis 2025-26": "PRS - தமிழ்நாடு வரவு செலவுத் திட்ட பகுப்பாய்வு 2025-26",
    "PRS - Profile of 16th Tamil Nadu Assembly": "PRS - 16-ஆவது தமிழ்நாடு சட்டமன்ற விவரக்குறிப்பு",
}

# Party detail table column label translations
COLUMN_LABEL_TA = {
    "Candidate": "வேட்பாளர்",
    "Education": "கல்வி",
    "Age": "வயது",
    "Criminal Cases": "குற்ற வழக்குகள்",
    "Total Assets Rs": "மொத்த சொத்து (₹)",
    "Liabilities Rs": "பாக்கிகள் (₹)",
    "Election Expenditure Rs": "தேர்தல் செலவு (₹)",
    "Party": "கட்சி",
    "2021 Constituency": "2021 தொகுதி",
    "2021 District": "2021 மாவட்டம்",
    "Education Formatted": "கல்வி விவரங்கள்",
    "Education Details": "கல்வி விவரங்கள்",
    "Legal Summary Short": "சட்ட வரலாறு",
    "Legal History": "சட்ட வரலாறு",
    "Self Profession": "தொழில்",
    "Spouse Profession": "துணைவர் தொழில்",
    "Myneta Url": "MyNeta இணைப்பு",
    "Sitting Mla": "தற்போதைய எம்.எல்.ஏ",
    "Sitting MLA": "தற்போதைய எம்.எல்.ஏ",
    "Constituency": "தொகுதி",
    "District": "மாவட்டம்",
    "Gender": "பாலினம்",
    "Total Assets (₹)": "மொத்த சொத்து (₹)",
    "Criminal cases": "குற்ற வழக்குகள்",
    "Education Details Clean": "கல்வி விவரங்கள்",
    "Criminal Cases Summary": "வழக்கு சுருக்கம்",
}

PROMISE_CATEGORY_TA = {
    "Agriculture": "விவசாயம்",
    "Culture & Language": "கலாச்சாரம் & மொழி",
    "Economy & Jobs": "பொருளாதாரம் & வேலைவாய்ப்பு",
    "Education": "கல்வி",
    "Environment": "சுற்றுச்சூழல்",
    "Governance": "ஆட்சி",
    "Health": "சுகாதாரம்",
    "Housing": "வீட்டுவசதி",
    "Infrastructure": "உள்கட்டமைப்பு",
    "Law & Order": "சட்டம் & ஒழுங்கு",
    "Social Welfare": "சமூக நலன்",
    "Water": "நீர்",
    "Women & Children": "பெண்கள் & குழந்தைகள்",
    "Other": "மற்றவை",
}

# Map legend translations
PARTY_NAME_TA = {
    "DMK": "தி.மு.க",
    "AIADMK": "அ.இ.அ.தி.மு.க",
    "IND": "சுயேச்சை",
    "Indian National Congress(INC)": "இந்திய தேசிய காங்கிரஸ்",
    "INC": "காங்கிரஸ்",
    "Bharatiya Janata Party": "பாரதிய ஜனதா கட்சி",
    "BJP": "பா.ஜ.க",
    "Communist Party of India  (Marxist)": "இந்திய கம்யூனிஸ்ட் கட்சி (மார்க்சிஸ்ட்)",
    "CPI(M)": "சி.பி.ஐ(எம்)",
    "Communist Party of India": "இந்திய கம்யூனிஸ்ட் கட்சி",
    "CPI": "சி.பி.ஐ",
    "Pattali Makkal Katchi": "பாட்டாளி மக்கள் கட்சி",
    "PMK": "பா.ம.க",
    "Viduthalai Chiruthaigal Katchi": "விடுதலைச் சிறுத்தைகள் கட்சி",
    "VCK": "வி.சி.க",
    "Naam Tamilar Katchi": "நாம் தமிழர் கட்சி",
    "NTK": "நா.த.க",
    "Makkal Needhi Maiam": "மக்கள் நீதி மையம்",
    "MNM": "ம.நீ.ம",
    "Desiya Murpokku Dravida Kazhagam": "தேசிய முற்போக்கு திராவிட கழகம்",
    "DMDK": "தே.மு.தி.க",
    "Marumalarchi Dravida Munnetra Kazhagam": "மறுமலர்ச்சி திராவிட முன்னேற்றக் கழகம்",
    "MDMK": "ம.தி.மு.க",
    "Amma Makkal Munnettra Kazagam": "அம்மா மக்கள் முன்னேற்றக் கழகம்",
    "AMMK": "அ.ம.மு.க",
    "Tamil Maanila Congress  (Moopanar)": "தமிழ்மாநில காங்கிரஸ் (மூப்பனார்)",
    "TMC(M)": "த.மா.கா(மூ)",
    "Kongunadu Munnetra Kazhagam": "கொங்குநாடு முன்னேற்றக் கழகம்",
    "Independent": "சுயேச்சை",
    "Independent / Unknown": "சுயேச்சை / தெரியாத",
}


def get_tamil_education(english_value: str) -> str:
    """Return Tamil translation of an education value, or the original if not found."""
    return EDUCATION_TA.get(english_value, english_value)


def get_tamil_status(english_value: str) -> str:
    """Return Tamil translation of a candidate status, or the original if not found."""
    return STATUS_TA.get(english_value, english_value)


def get_tamil_gender(english_value: str) -> str:
    """Return Tamil translation of a gender value, or the original if not found."""
    return GENDER_TA.get(english_value, english_value)


def get_tamil_party_name(english_name: str) -> str:
    """Return Tamil translation of a party name, or the original if not found."""
    return PARTY_NAME_TA.get(english_name, english_name)
