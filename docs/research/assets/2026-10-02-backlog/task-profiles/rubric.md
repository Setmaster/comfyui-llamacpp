## Scoring rubric fixed before output collection

Each measured response receives a score from 0 to 10:

`max(0, 4 * mean(fact scores) + 4 * mean(constraint passes) + clarity - hallucination penalty)`

- **Facts, maximum 4:** score every listed fact 1 when correctly and fully
  retained/described, 0.5 when recognizable but incomplete, and 0 when missing,
  contradicted or wrong. A synonym can earn full credit. For example, "off-white"
  and "cream" are equivalent here; "two blue squares" cannot become "blue shapes"
  without losing specificity. Cite the response span or state what is missing.
- **Constraints, maximum 4:** each explicit case constraint is pass/fail. Check
  word limits, paragraph/two-line format, forbidden additions and medium or
  visible-evidence limits. A failed automatic word/line check cannot be marked
  passing; passing automatic checks do not prove the remaining semantic rules.
  Word counts use whitespace-separated tokens. In the operator-format case,
  the 40-word limit applies only to its second line.
- **Clarity, maximum 2:** 2 means directly usable, coherent and free of redundant
  or distracting phrasing; 1 means understandable but requires minor editing;
  0 means unusable, contradictory or largely irrelevant. Longer output earns no
  intrinsic benefit. Provide a short reason.
- **Unsupported additions, penalty up to 4:** subtract 2 per distinct unsupported
  asserted detail, capped at 4. Quote each claim and explain the absent evidence.
  A wrong listed fact already loses fact credit; do not also list that same error
  as an added claim. A genuinely invented detail can also violate an explicit
  exclusion. Hedging does not excuse an invented detail when the task forbids
  inference. Ordinary visual shape labels supported by pixels are allowed.
- **Failure:** failed, timed-out or nonterminal generation scores 0 and cannot
  qualify a candidate for follow-up. A stopped/incomplete batch must be reported
  as incomplete, not summarized as if missing pairs passed.

All criteria require explicit evidence and a reviewed flag. Record "no
unsupported claims" as an empty reviewed list. The scorer checks geometry/OCR
against the PNG, not keyword presence alone. Synthetic unit-test scores prove
the aggregator's behavior and are never quality evidence.
