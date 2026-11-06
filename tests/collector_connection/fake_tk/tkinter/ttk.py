from . import Widget
class Notebook(Widget):
    def add(self, child, text=""): child.tab_text = text
