from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderQueryError, GeocoderTimedOut
from timezonefinder import TimezoneFinder
import time

_tf = TimezoneFinder()
_geoloc = Nominatim(
    user_agent="talk-bot/1.0 (aigumenov@yandex.ru)",
    timeout=10,  # seconds
)

def geocode_city(city: str, retries: int = 3) -> dict:
    location = None
    for attempt in range(retries):
        try:
            location = _geoloc.geocode(city, language="ru", exactly_one=True)
            break
        except GeocoderTimedOut:
            if attempt == retries - 1:
                return {"error": "Geocoder timed out after retries"}
            time.sleep(1.5 * (attempt + 1))
        except (GeocoderServiceError, GeocoderQueryError) as exc:
            return {"error": f"Geocoder error: {exc}"}

    if not location:
        return {"error": f"City '{city}' not found"}

    lat = float(location.latitude)
    lon = float(location.longitude)

    tz = _tf.timezone_at(lat=lat, lng=lon) or "UTC"

    return {
        "name": city,
        "timezone": tz,
        "latitude": lat,
        "longitude": lon,
    }


if __name__ == "__main__":
    city = input("Enter the city: ")
    info = geocode_city(city)
    if "error" in info:
        print(info["error"])
    else:
        print(f"City: {info['name']}")
        print(f"Timezone: {info['timezone']}")
        print(f"Latitude: {info['latitude']}")
        print(f"Longitude: {info['longitude']}")