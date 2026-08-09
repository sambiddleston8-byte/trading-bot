# London Demonstration Handover

## Monday route: portable briefing, not a network-dependent app

Use `Portfolio_Construction_Prototype_Report.docx` as the portable briefing
for Pat. It contains the latest saved proposed portfolio, the allocation
visual, the research and monitoring capabilities, safeguards, benchmark plan
and the next development priorities. It works without depending on a specific
Wi-Fi network.

The local website remains the working prototype. Its two core views are:

1. **Proposed portfolio** — sector allocation, decision ratings and links to
   each company's full research report.
2. **Current portfolio** — latest available-price, refreshed research, thesis,
   audit and proposed allocation-change reviews. Price movement is context,
   not a fixed sell rule, and it never places trades automatically.

If a remote live website is wanted later, deploy only after a separate review
of the code, provider limits and secret handling. Do not add keys to GitHub or
to this document.

## Security boundary

The prototype is research and paper monitoring only. It contains no broker
connection, cannot place orders, and all changes remain review prompts rather
than sell orders.
