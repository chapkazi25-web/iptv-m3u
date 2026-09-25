PYTHON ?= python3

.PHONY: catalog epg logos apply-logos build validate test health refresh

catalog:
	$(PYTHON) scripts/discover/build_catalog.py

epg:
	$(PYTHON) scripts/epg/import_epgshare.py

logos:
	$(PYTHON) scripts/logos/remote_index.py

apply-logos:
	$(PYTHON) scripts/logos/apply_logos.py

build:
	$(PYTHON) scripts/build/merge_playlists.py

validate:
	$(PYTHON) scripts/validate/validate_m3u.py

test:
	$(PYTHON) -m unittest discover -v

health:
	$(PYTHON) scripts/test/check_streams.py

refresh: catalog logos build apply-logos validate
