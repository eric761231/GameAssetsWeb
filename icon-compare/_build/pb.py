"""最小 protobuf 解碼（無 schema）：回傳 [(欄位號, 型別, 值)]，型別 0=varint、2=bytes、1=64 位、5=32 位。"""


def varint(b, p):
    v = 0
    s = 0
    while True:
        c = b[p]
        p += 1
        v |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return v, p


def fields(b):
    out = []
    p = 0
    n = len(b)
    while p < n:
        key, p = varint(b, p)
        f = key >> 3
        t = key & 7
        if t == 0:
            v, p = varint(b, p)
        elif t == 2:
            ln, p = varint(b, p)
            v = b[p:p + ln]
            p += ln
        elif t == 1:
            v = b[p:p + 8]
            p += 8
        elif t == 5:
            v = b[p:p + 4]
            p += 4
        else:
            raise ValueError('wire type %d at %d' % (t, p))
        out.append((f, t, v))
    return out
