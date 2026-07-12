from django.conf import settings


def language_toggle(request):
    lang = request.session.get("language", "en")
    return {"current_language": lang}

