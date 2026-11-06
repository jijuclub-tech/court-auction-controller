"""PowerShell 파일의 괄호·따옴표 짝 검사 (윈도우가 없는 곳에서 할 수 있는 최소 점검)"""
import sys
def check(path):
    s = open(path, encoding="utf-8-sig").read()
    i, n, stack, line = 0, len(s), [], 1
    pairs = {"{": "}", "(": ")", "[": "]"}
    while i < n:
        c = s[i]
        if c == "\n": line += 1
        if s.startswith('@"', i) and (i + 2 < n and s[i+2] == "\n"):
            j = s.find('\n"@', i); line += s.count("\n", i, j + 3); i = j + 3; continue
        if s.startswith("<#", i):
            j = s.find("#>", i); line += s.count("\n", i, j); i = j + 2; continue
        if c == "#":
            j = s.find("\n", i); i = j; continue
        if c == "'":
            j = i + 1
            while True:
                j = s.find("'", j)
                if s.startswith("''", j): j += 2; continue
                break
            line += s.count("\n", i, j); i = j + 1; continue
        if c == '"':
            j = i + 1
            while j < n:
                if s[j] == "`": j += 2; continue
                if s[j] == '"':
                    if j + 1 < n and s[j+1] == '"': j += 2; continue
                    break
                j += 1
            line += s.count("\n", i, j); i = j + 1; continue
        if c in pairs: stack.append((c, line))
        elif c in pairs.values():
            if not stack or pairs[stack[-1][0]] != c:
                print(f"{path}: 짝 안 맞음 '{c}' line {line}, 열린 것 {stack[-1] if stack else None}"); return False
            stack.pop()
        i += 1
    if stack: print(f"{path}: 닫히지 않음 {stack[-3:]}"); return False
    print(f"{path}: 괄호·따옴표 짝 OK ({line}줄)"); return True
ok = all(check(p) for p in sys.argv[1:]); sys.exit(0 if ok else 1)
