from . import Widget
class ScrolledText(Widget):
    def insert(self, idx, v): self._text += str(v)
    def delete(self, *a): self._text = ""
    def get(self, *a): return self._text
    def see(self, *a): pass
