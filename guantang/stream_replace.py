import re


class StreamReplacer:
    def __init__(self, rules: list[dict]):
        self.items = []
        for rule in rules or []:
            pattern = str(rule.get("pattern") or "").strip()
            replacement = str(rule.get("replacement") or "")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error:
                continue
            self.items.append((compiled, replacement))
        self.buf = ""
        self.out = ""

    def feed(self, delta: str):
        self.buf += delta
        replaced = self.buf
        for compiled, replacement in self.items:
            replaced = compiled.sub(replacement, replaced)
        if replaced.startswith(self.out):
            new = replaced[len(self.out):]
            back = 0
        else:
            i = 0
            while i < len(replaced) and i < len(self.out) and replaced[i] == self.out[i]:
                i += 1
            back = len(self.out) - i
            new = replaced[i:]
        self.out = replaced
        return new, back

    def flush(self):
        self.buf = ""
        self.out = ""
        return ""
