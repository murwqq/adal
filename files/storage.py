"""Хранилище Django, которое пишет файлы в таблицу StoredFile."""
import mimetypes
import posixpath
import secrets

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.urls import reverse
from django.utils.deconstruct import deconstructible


@deconstructible
class DatabaseStorage(Storage):
    def _model(self):
        from .models import StoredFile
        return StoredFile

    def _open(self, name, mode="rb"):
        obj = self._model().objects.get(name=name)
        f = ContentFile(bytes(obj.content), name=name)
        return f

    def _save(self, name, content):
        content.seek(0)
        data = content.read()
        if isinstance(data, str):
            data = data.encode()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._model().objects.create(name=name, content=data, content_type=content_type, size=len(data))
        return name

    def get_available_name(self, name, max_length=None):
        name = name.replace("\\", "/")
        while self.exists(name):
            root, ext = posixpath.splitext(name)
            name = f"{root}_{secrets.token_hex(3)}{ext}"
        return name

    def exists(self, name):
        return self._model().objects.filter(name=name).exists()

    def delete(self, name):
        self._model().objects.filter(name=name).delete()

    def size(self, name):
        return self._model().objects.values_list("size", flat=True).get(name=name)

    def url(self, name):
        return reverse("file", args=[name])
