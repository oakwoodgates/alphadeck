"""Row <-> domain mappers + thesis/calls repositories.

The explicit persistence seam: raw DB rows are turned into domain objects (and back) ONLY here, so
no caller ever handles a raw row. Detectors/the assembler stay pure; persistence lives behind this.
"""
