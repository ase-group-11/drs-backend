"""

Mapbox Map Provider - Integration with Mapbox Maps API.

Provides base map tiles configuration for the live map feature.
Handles tile URL generation, style management, and map initialization
with default center/zoom for Dublin.

API Documentation: https://docs.mapbox.com/api/maps/styles/

"""

# Example usage
"""
# Initialize provider
map_provider = MapProvider(
    api_key="your-mapbox-api-key",
    style="streets-v12",
    tilesize=256
)

# Get tiles configuration
tiles = await map_provider.get_base_map_tiles(
    center={"lat": 53.3498, "lon": -6.2603},
    zoom=12
)

# Response structure:
{
    "tiles_url": "https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/256/{z}/{x}/{y}@2x?access_token=pk.xxx",
    "attribution": "© Mapbox © OpenStreetMap",
    "style": "streets-v12",
    "zoom": 12,
    "center": {"lat": 53.3498, "lon": -6.2603}
}

# Get map initialization config (includes defaults)
init = await map_provider.get_map_init(
    lat=53.3498, lon=-6.2603, zoom=12
)

# Check available styles
styles = map_provider.get_available_styles()
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class MapProvider:
    """
    Integration with Mapbox for base map tiles.

    Provides:
    - Tile URL template generation for vector/raster tiles
    - Map style management (streets, satellite, dark, light, etc.)
    - Map initialization configuration (center, zoom, bounds)
    
    Uses Mapbox Styles API for serving map tiles to the frontend.
    The frontend uses these tile URLs with a map rendering library
    (e.g., Mapbox GL JS, MapLibre GL) to display the visual map.
    """

    # Mapbox tile URL base
    TILES_BASE_URL = "https://api.mapbox.com/styles/v1/mapbox"

    # Available map styles
    STYLES = {
        "streets": "streets-v12",
        "outdoors": "outdoors-v12",
        "light": "light-v11",
        "dark": "dark-v11",
        "satellite": "satellite-v9",
        "satellite-streets": "satellite-streets-v12",
        "navigation-day": "navigation-day-v1",
        "navigation-night": "navigation-night-v1",
    }

    DEFAULT_STYLE = "streets-v12"
    ATTRIBUTION = "© Mapbox © OpenStreetMap"

    # Zoom constraints
    MIN_ZOOM = 1
    MAX_ZOOM = 20

    def __init__(
        self,
        api_key: str,
        style: str = "streets-v12",
        tilesize: int = 256,
        retina: bool = True,
    ):
        """
        Initialize the Mapbox provider.

        Args:
            api_key: Mapbox access token (starts with "pk.")
            style: Mapbox style ID (e.g., "streets-v12", "dark-v11")
            tilesize: Tile size in pixels (256 or 512)
            retina: Whether to use @2x retina tiles for high-DPI displays
        """
        self.api_key = api_key
        self.style = style
        self.tilesize = tilesize
        self.retina = retina

        if not api_key:
            logger.warning(
                "MapProvider initialized without API key — tiles will not load"
            )

    async def get_base_map_tiles(
        self,
        center: Dict[str, float],
        zoom: int,
        style: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get the base map tiles configuration for the frontend.

        This does NOT fetch actual image tiles — it returns a URL template
        that the frontend map library uses to request tiles as the user
        pans and zooms.

        Args:
            center: Dict with 'lat' and 'lon' keys
            zoom: Zoom level (1-20)
            style: Optional style override (uses instance default if None)

        Returns:
            Dict containing:
                - tiles_url: Template URL with {z}/{x}/{y} placeholders
                - attribution: Map attribution text (required by Mapbox TOS)
                - style: Style name used
                - zoom: Clamped zoom level
                - center: Center coordinates passed through
        """
        active_style = style or self.style
        clamped_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))

        retina_suffix = "@2x" if self.retina else ""
        tiles_url = (
            f"{self.TILES_BASE_URL}/{active_style}/tiles"
            f"/{self.tilesize}/{{z}}/{{x}}/{{y}}{retina_suffix}"
            f"?access_token={self.api_key}"
        )

        logger.info(
            f"Generated tiles URL for style={active_style}, "
            f"zoom={clamped_zoom}, center={center}"
        )

        return {
            "tiles_url": tiles_url,
            "attribution": self.ATTRIBUTION,
            "style": active_style,
            "zoom": clamped_zoom,
            "center": center,
        }

    async def get_map_init(
        self,
        lat: float,
        lon: float,
        zoom: int,
        style: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get full map initialization configuration.

        Returns everything the frontend needs to set up the map view
        on first load, including center, zoom, bounds, and tiles URL.

        Args:
            lat: Center latitude
            lon: Center longitude
            zoom: Initial zoom level
            style: Optional style override

        Returns:
            Dict with center, zoom, location_name, bounds, and tiles config
        """
        center = {"lat": lat, "lon": lon}
        clamped_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))

        tiles_config = await self.get_base_map_tiles(
            center=center, zoom=clamped_zoom, style=style
        )

        # Approximate visible bounds based on zoom level
        # At zoom 12, roughly 0.05 degrees lat/lon visible
        delta_lat = 180 / (2 ** clamped_zoom)
        delta_lon = 360 / (2 ** clamped_zoom)

        bounds = {
            "south": lat - delta_lat,
            "west": lon - delta_lon,
            "north": lat + delta_lat,
            "east": lon + delta_lon,
        }

        return {
            "center": center,
            "zoom": clamped_zoom,
            "location_name": "Dublin, Ireland",  # Could be reverse-geocoded
            "bounds": bounds,
            "tiles": tiles_config,
        }

    def get_available_styles(self) -> List[Dict[str, str]]:
        """
        Return list of available map styles.

        Returns:
            List of dicts with 'name' (friendly) and 'id' (Mapbox style ID)
        """
        return [
            {"name": name, "id": style_id}
            for name, style_id in self.STYLES.items()
        ]

    def is_valid_style(self, style: str) -> bool:
        """
        Check whether a style ID is valid.

        Args:
            style: Mapbox style ID to validate

        Returns:
            True if style is a known valid style
        """
        all_ids = set(self.STYLES.values())
        return style in all_ids
 