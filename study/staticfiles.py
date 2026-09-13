"""Expose only the shared modules required by the website renderer."""
from django.conf import settings
from django.contrib.staticfiles.finders import BaseFinder
from django.core.files.storage import FileSystemStorage


class WebsiteModuleFinder(BaseFinder):
    modules = ('config.js', 'website-state.js', 'rendering.js', 'level-circles.js', 'animation.js', 'website-graph.js')

    def __init__(self, *args, **kwargs):
        self.storage = FileSystemStorage(location=settings.BASE_DIR / 'js')
        self.storage.prefix = 'js'
        super().__init__(*args, **kwargs)

    def find(self, path, find_all=False, **kwargs):
        if path.startswith('js/') and path[3:] in self.modules and self.storage.exists(path[3:]):
            result = self.storage.path(path[3:])
            return [result] if find_all else result
        return [] if find_all else None

    def list(self, ignore_patterns):
        for module in self.modules:
            if self.storage.exists(module):
                yield module, self.storage
