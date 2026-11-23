from django.http import Http404, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET

from .models import StoredFile


@login_required
@require_GET
def serve(request, name):
    obj = StoredFile.objects.filter(name=name).only("content", "content_type").first()
    if obj is None:
        raise Http404
    response = HttpResponse(bytes(obj.content), content_type=obj.content_type)
    # Имена файлов уникальные и не меняются, поэтому их можно долго кэшировать.
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
