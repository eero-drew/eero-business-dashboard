#!/usr/bin/env python3
"""
eero Business Dashboard - Geocoding Service

Supports two providers:
  1. Google Geocoding API (requires GOOGLE_MAPS_API_KEY)
  2. OpenStreetMap Nominatim (free, no key needed) — used as fallback

The service automatically picks the first available provider.
"""
import os
import logging
import time

import requests


class GeocodingService:
    """Geocode addresses to lat/lng coordinates."""

    def __init__(self, api_key=None):
        self.google_api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")

    def validate_address(self, address_dict):
        """Validate that street, city, and state are present."""
        required = ["street", "city", "state"]
        return all(address_dict.get(f) for f in required)

    def geocode(self, address_dict):
        """
        Convert an address dict to lat/lng coordinates.

        Returns:
            {"lat": float, "lng": float, "formatted": str} on success, or None.
        """
        if self.google_api_key:
            result = self._geocode_google(address_dict)
            if result:
                return result

        # Fallback to free Nominatim
        return self._geocode_nominatim(address_dict)

    def _build_address_string(self, address_dict):
        parts = [
            address_dict.get("street", ""),
            address_dict.get("city", ""),
            address_dict.get("state", ""),
        ]
        if address_dict.get("zip"):
            parts.append(address_dict["zip"])
        if address_dict.get("country"):
            parts.append(address_dict["country"])
        return ", ".join(p for p in parts if p)

    # ------------------------------------------------------------------
    # Google Geocoding API
    # ------------------------------------------------------------------

    def _geocode_google(self, address_dict):
        address_str = self._build_address_string(address_dict)
        try:
            response = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address_str, "key": self.google_api_key},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                location = result["geometry"]["location"]
                return {
                    "lat": location["lat"],
                    "lng": location["lng"],
                    "formatted": result.get("formatted_address", address_str),
                }
            logging.warning("Google geocoding failed for '%s': %s", address_str, data.get("status"))
            return None
        except requests.RequestException as exc:
            logging.error("Google geocoding error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # OpenStreetMap Nominatim (free, no API key)
    # ------------------------------------------------------------------

    def _geocode_nominatim(self, address_dict):
        """Geocode using OSM Nominatim. Free, rate-limited to 1 req/sec."""
        address_str = self._build_address_string(address_dict)
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": address_str,
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 1,
                },
                headers={"User-Agent": "eero-Business-Dashboard/1.0"},
                timeout=10,
            )
            response.raise_for_status()
            results = response.json()

            if results:
                result = results[0]
                return {
                    "lat": float(result["lat"]),
                    "lng": float(result["lon"]),
                    "formatted": result.get("display_name", address_str),
                }

            logging.warning("Nominatim: no results for '%s'", address_str)
            return None
        except requests.RequestException as exc:
            logging.error("Nominatim geocoding error: %s", exc)
            return None
        except (KeyError, ValueError) as exc:
            logging.error("Nominatim parse error: %s", exc)
            return None
