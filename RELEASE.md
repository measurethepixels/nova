# Release provenance

Source revision: `fd471be00c23a85fbf57233007f00601e8c8b89e`

This tree passed the export scrub scan and its generated files are listed in `SHA256SUMS`. A Git tag is a separate publication decision: tag only the public commit whose CI smoke check is green.

## Clean-room status

The dependency/install smoke check is automated in `.github/workflows/public-smoke.yml`. It also validates sample frame checksums and scans their FITS headers for private location metadata. End-to-end image verification remains pending until golden metrics have evidence from two genuinely different machines and the REPLICATE.md protocol passes on a clean machine.
