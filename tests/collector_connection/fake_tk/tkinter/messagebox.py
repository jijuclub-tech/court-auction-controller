SHOWN = []
def showerror(*a): SHOWN.append(("error",) + a)
def showinfo(*a): SHOWN.append(("info",) + a)
def showwarning(*a): SHOWN.append(("warning",) + a)
