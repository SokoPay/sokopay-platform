"""
Reconciliation engine: compare what SokoPay thinks happened ("ours") with what the
rail/partner reports ("theirs"), and classify every reference.

Pure and side-effect-free, so it is easy to test exhaustively. Both inputs are maps of
{provider_reference: amount_in_minor_units}. The result sorts every reference into one
of four buckets — this is the heart of catching lost, duplicated or mis-stated money.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReconResult:
    matched: list[str] = field(default_factory=list)              # refs, equal amounts
    amount_mismatch: list[tuple[str, int, int]] = field(default_factory=list)  # (ref, ours, theirs)
    ours_only: list[tuple[str, int]] = field(default_factory=list)   # we have it, rail doesn't
    theirs_only: list[tuple[str, int]] = field(default_factory=list)  # rail has it, we don't

    @property
    def is_clean(self) -> bool:
        return not (self.amount_mismatch or self.ours_only or self.theirs_only)


def classify(ours: dict[str, int], theirs: dict[str, int]) -> ReconResult:
    result = ReconResult()
    our_refs, their_refs = set(ours), set(theirs)

    for ref in sorted(our_refs & their_refs):
        if ours[ref] == theirs[ref]:
            result.matched.append(ref)
        else:
            result.amount_mismatch.append((ref, ours[ref], theirs[ref]))

    for ref in sorted(our_refs - their_refs):
        result.ours_only.append((ref, ours[ref]))

    for ref in sorted(their_refs - our_refs):
        result.theirs_only.append((ref, theirs[ref]))

    return result
