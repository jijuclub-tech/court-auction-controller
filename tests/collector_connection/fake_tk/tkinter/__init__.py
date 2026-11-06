"""시험용 가짜 tkinter: 화면은 그리지 않고 글자·버튼·입력값만 흉내낸다."""
END = "end"; LEFT = "left"; RIGHT = "right"; X = "x"; BOTH = "both"; WORD = "word"
DISABLED = "disabled"; NORMAL = "normal"
WIDGETS = []
class _Var:
    def __init__(self, master=None, value=None): self._v = value
    def get(self): return self._v
    def set(self, v): self._v = v
class StringVar(_Var):
    def __init__(self, master=None, value=""): super().__init__(master, value)
class BooleanVar(_Var):
    def __init__(self, master=None, value=False): super().__init__(master, value)
class IntVar(_Var):
    def __init__(self, master=None, value=0): super().__init__(master, value)
class Widget:
    def __init__(self, master=None, **kw):
        self.kw = kw; self.master = master; self._text = ""; WIDGETS.append(self)
    def pack(self, *a, **k): return self
    def config(self, **kw): self.kw.update(kw)
    configure = config
    def bind(self, *a, **k): pass
    def destroy(self): pass
    def transient(self, *a): pass
    def grab_set(self): pass
    def geometry(self, *a): pass
    def title(self, *a): pass
    def resizable(self, *a): pass
    def minsize(self, *a): pass
    def wait_window(self, *a): pass
    def after(self, ms, fn=None, *args):
        if fn and ms == 0: fn(*args)
    def mainloop(self): pass
    @property
    def text(self):
        tv = self.kw.get("textvariable")
        return tv.get() if tv else self.kw.get("text", "")
class Tk(Widget): pass
class Toplevel(Widget): pass
class Frame(Widget): pass
class Label(Widget): pass
class Button(Widget): pass
class Checkbutton(Widget): pass
class Radiobutton(Widget): pass
class Listbox(Widget):
    def __init__(self, *a, **k): super().__init__(*a, **k); self.items = []
    def delete(self, *a): self.items = []
    def insert(self, idx, v): self.items.append(v)
class Entry(Widget):
    def insert(self, idx, v): self._text = self._text + str(v)
    def delete(self, *a): self._text = ""
    def get(self): return self._text
