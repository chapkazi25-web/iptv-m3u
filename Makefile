PYTHON ?= python3

.PHONY: catalog epg logos languages apply-logos build validate test health sample refresh \
        sources-gradetv sources-iptv-org

catalog:
	$(PYTHON) scripts/discover/build_catalog.py

epg:
	$(PYTHON) scripts/epg/import_epgshare.py

logos:
	$(PYTHON) scripts/logos/remote_index.py

languages:
	$(PYTHON) scripts/languages/build_index.py

apply-logos:
	$(PYTHON) scripts/logos/apply_logos.py

build:
	$(PYTHON) scripts/build/merge_playlists.py

validate:
	$(PYTHON) scripts/validate/validate_m3u.py

test:
	$(PYTHON) -m unittest discover -v

health:
	$(PYTHON) scripts/validate/check_streams.py --workers 64 --timeout 10 --max-per-host 16

# Probe a sample of every source and report the play rate per source.
sample:
	$(PYTHON) scripts/validate/sample_sources.py --per-source 20

sources-gradetv:
	$(PYTHON) sources/gradetv/generate.py

sources-iptv-org:
	$(PYTHON) sources/iptv-org/generate.py

refresh: catalog logos build apply-logos validate
