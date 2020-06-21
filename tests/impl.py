def split_by(seq, n):
    seq = seq
    while seq:
        yield seq[:n]
        seq = seq[n:]
