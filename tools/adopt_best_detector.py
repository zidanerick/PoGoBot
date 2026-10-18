#!/usr/bin/env python3
"""Retired recall-only selector. Use evaluate_detector.py for reviewed evaluation.

Automatically installing a recall winner ignored class errors, duplicate detections,
false positives and data leakage. Evaluation now produces evidence for human review;
it never changes the deployed weights.
"""


def main() -> int:
    print("Recall-only adoption is disabled. Use tools/evaluate_detector.py with a reviewed "
          "manifest and explicit class map. See docs/PERCEPTION_EVALUATION.md. "
          "No weights were changed.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
