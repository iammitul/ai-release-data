# AI release data

A backup copy of AI model release data, used by the AI Release Tracker Android
app only when its main source (models.dev) cannot be reached.

- `models.json` is rebuilt every 6 hours by `.github/workflows/update.yml`.
- The data comes from the open source models.dev catalog
  (https://github.com/anomalyco/models.dev, MIT license).
- `tools/build_data.py` does the conversion. It keeps only the fields the app
  reads: name, release date, type, modalities, limits, links and benchmarks.
