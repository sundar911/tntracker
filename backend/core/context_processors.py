from django.conf import settings


def language_toggle(request):
    lang = request.session.get("language", "en")
    return {"current_language": lang}


def data_vintage(request):
    return {"data_vintage_label": getattr(settings, "DATA_VINTAGE_LABEL", "")}
