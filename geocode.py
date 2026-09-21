from geopy.geocoders import Nominatim
import numpy as np

def geocode_city(city: str) -> dict:
    geoloc = Nominatim(user_agent="geopy_test")
    location = geoloc.geocode(city)
    
    if not location:
        return {"error": f"City '{city}' not found"}
    
    # Use attributes instead of unpacking to avoid (lat, lon, name) tuple issues
    lat = location.latitude
    lon = location.longitude
    
    return {
        "name": city,
        "timezone": "Europe/Moscow",
        "latitude": float(lat),
        "longitude": float(lon)
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
