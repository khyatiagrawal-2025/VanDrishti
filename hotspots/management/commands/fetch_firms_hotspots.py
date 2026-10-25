import os
from django.core.management.base import BaseCommand
from hotspots.data_pipeline import fetch_firms_live_data, import_observations_from_csv


class Command(BaseCommand):
    help = (
        "Fetch live satellite thermal anomaly observations from NASA FIRMS Area API "
        "for the Nainital Forest Division study area."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--key",
            type=str,
            default="",
            help="32-character NASA FIRMS MAP_KEY (defaults to os.environ.get('FIRMS_MAP_KEY'))",
        )
        parser.add_argument(
            "--source",
            type=str,
            default="VIIRS_NOAA20_NRT",
            choices=["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"],
            help="Satellite sensor stream (default: VIIRS_NOAA20_NRT)",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="Days range (1 to 5, default: 1)",
        )
        parser.add_argument(
            "--bbox",
            type=str,
            default="79.10,29.10,79.80,29.65",
            help="Bounding box 'min_lng,min_lat,max_lng,max_lat' (default: Nainital Division)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate schema and simulate scoring without committing writes to the database",
        )
        parser.add_argument(
            "--no-filter-bounds",
            action="store_true",
            help="Do not filter out observations falling outside the strict Nainital district boundary",
        )
        parser.add_argument(
            "--sample-fallback",
            action="store_true",
            help="If no API key is provided or the connection fails, safely import historical FIRMS sample data for offline demonstration",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        key = (
            options.get("key")
            or getattr(settings, "FIRMS_MAP_KEY", None)
            or os.environ.get("FIRMS_MAP_KEY", "")
        )
        source = options["source"]
        days = options["days"]
        bbox = options["bbox"]
        dry_run = options["dry_run"]
        filter_bounds = not options["no_filter_bounds"]
        sample_fallback = options["sample_fallback"]

        self.stdout.write(
            f"Querying NASA FIRMS ({source}) for Nainital Division [{bbox}] over last {days} day(s)..."
        )

        res = fetch_firms_live_data(
            map_key=key,
            source=source,
            day_range=days,
            area_bbox=bbox,
            dry_run=dry_run,
            filter_bounds=filter_bounds,
        )

        if res["success"]:
            if res.get("status") == "no_anomalies":
                self.stdout.write(self.style.SUCCESS(f"FIRMS Query OK: {res.get('message')}"))
            elif dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Dry run OK: {res['valid_records']} valid observations found ({res['skipped']} skipped)."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Live sync complete: {res['created']} created, {res['updated']} updated, {res['skipped']} skipped."
                    )
                )
            return

        # If we reached here, live fetch did not succeed
        self.stdout.write(self.style.WARNING(f"Live FIRMS fetch failed ({res.get('status')}): {res.get('error')}"))

        if sample_fallback:
            fallback_csv = os.path.join("hotspots", "data", "historical_firms_nainital_sample.csv")
            self.stdout.write(self.style.NOTICE(f"Executing offline demonstration fallback using '{fallback_csv}'..."))
            fb_res = import_observations_from_csv(
                fallback_csv,
                default_data_nature="historical",
                dry_run=dry_run,
                filter_bounds=filter_bounds,
            )
            if fb_res["success"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Offline fallback loaded: {fb_res.get('created', 0)} created, {fb_res.get('updated', 0)} updated (classified as 'historical')."
                    )
                )
            else:
                self.stdout.write(self.style.ERROR(f"Fallback import failed: {fb_res.get('error')}"))
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "To use offline sample data, rerun with --sample-fallback, or configure FIRMS_MAP_KEY."
                )
            )
