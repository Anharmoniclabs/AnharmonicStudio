# Refinement cycle — track recording monitor

Verified on the current main branch: Song-track recording exposes input gain and monitor controls, but those controls currently read/write the global vocal-record settings and generic Song audio capture passes no monitor callback. This cycle targets making the Song-track monitor truthful and track-local without changing the core project format.
