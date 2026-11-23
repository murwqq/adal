"""Загруженные файлы (картинки вопросов, аватарки) хранятся прямо в базе.

На Vercel у каждого экземпляра сервера своя временная папка, и файл,
сохранённый одним экземпляром, другой просто не видит. В базе картинка
лежит рядом с вопросом или профилем, к которому относится."""
from django.db import models


class StoredFile(models.Model):
    name = models.CharField(max_length=255, unique=True)
    content = models.BinaryField()
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "файл"
        verbose_name_plural = "файлы"

    def __str__(self):
        return self.name
