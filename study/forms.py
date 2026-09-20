"""Multipart contract for P5: `images` and `image_path` are paired by index.

`markdown` may be a text field, or `markdown_file` may contain UTF-8 Markdown.
The duplicate-preserving getlist handling intentionally happens before dict creation.
"""
from uuid import UUID

from django import forms


class ArticleSubmissionForm(forms.Form):
    title = forms.CharField(required=False, max_length=200)
    color = forms.CharField(max_length=7)
    markdown = forms.CharField(required=False, strip=False, widget=forms.Textarea)
    markdown_file = forms.FileField(required=False)
    submission_id = forms.UUIDField(required=False)
    revision = forms.IntegerField(required=False, min_value=1)
    article_id = forms.UUIDField(required=False)

    def clean(self):
        cleaned = super().clean()
        markdown_file = cleaned.get("markdown_file")
        markdown = cleaned.get("markdown")
        if markdown_file:
            try:
                markdown = markdown_file.read().decode("utf-8")
            except UnicodeDecodeError:
                raise forms.ValidationError("Markdown must be UTF-8.")
        if markdown in (None, ""):
            raise forms.ValidationError("Markdown is required.")
        cleaned["markdown"] = markdown
        files = self.files.getlist("images")
        paths = self.data.getlist("image_path") if hasattr(self.data, "getlist") else []
        if len(files) != len(paths):
            raise forms.ValidationError("Every image requires one image_path.")
        images = {}
        for path, upload in zip(paths, files, strict=True):
            if path in images:
                raise forms.ValidationError("Duplicate image path.")
            if upload.size > 10 * 1024 * 1024:
                raise forms.ValidationError("Image is too large.")
            images[path] = upload.read()
        cleaned["images"] = images
        return cleaned
