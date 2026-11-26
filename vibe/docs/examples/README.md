# Example and evaluation inputs

All committed examples are synthetic or templates, not the BCC organizer's official specification. They are frozen evaluation inputs; do not modify a gold file after examining a model answer to raise its measured result.

- [fintech-payment-spec.txt](fintech-payment-spec.txt) and [synthetic references](synthetic-references/) exercise the optional reference comparison with [a runnable manifest](reference-manifest.example.json).
- [organizer-eval](organizer-eval/README.md) demonstrates the offline evaluation data contracts with a separate synthetic source/report; it does not contain the private final delivery output.
- [quality-benchmark](quality-benchmark/) contains the four default defective/revised training cases and human labels, historical CSV measurements and a business-value scenario. [Extended cases](quality-benchmark/extended/) are two additional frozen synthetic cases used for fact-index and formal-witness experiments; they are not implicitly added to the default matrix.
- [reference-manifest.template.json](reference-manifest.template.json) is a template to populate with authorized files. It does not download data from URLs.

See [quality methodology and dated results](../QUALITY_EVALUATION.md), [retention evaluation](../REVIEW_RETENTION.md) and [reference scope](../REGULATORY_CONTEXT.md). Download the official source separately for acceptance runs.
