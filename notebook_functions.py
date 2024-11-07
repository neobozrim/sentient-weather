from geopy.geocoders import Nominatim
import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry
from anthropic import Anthropic
from openai import OpenAI
import json

def get_city_coordinates(city):
    """Get coordinates for a given city."""
    try:
        geolocator = Nominatim(user_agent="sentient-weather-app")
        location = geolocator.geocode(city)
        return (location.latitude, location.longitude)
    except:
        return "City not found"

def get_weather_data(latitude, longitude):
    """Get current weather and forecast data from Open Meteo API."""
    try:
        # Setup the Open-Meteo API client with cache and retry
        cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
        retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
        openmeteo = openmeteo_requests.Client(session=retry_session)

        # API parameters
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": ["temperature_2m", "is_day", "precipitation", 
                       "weather_code", "cloud_cover", "wind_speed_10m"],
            "daily": ["weather_code", "temperature_2m_max", "temperature_2m_min",
                     "precipitation_sum", "precipitation_hours",
                     "precipitation_probability_max", "wind_speed_10m_max"]
        }

        # Make the API request
        response = openmeteo.weather_api(url, params=params)[0]
        
        # Process current weather
        current = response.Current()
        current_data = {
            'temperature': current.Variables(0).Value(),
            'is_day': current.Variables(1).Value(),
            'precipitation': current.Variables(2).Value(),
            'weather_code': current.Variables(3).Value(),
            'cloud_cover': current.Variables(4).Value(),
            'wind_speed': current.Variables(5).Value(),
        }

        # Process forecast
        daily = response.Daily()
        forecast_data = pd.DataFrame({
            "date": pd.date_range(
                start=pd.to_datetime(daily.Time(), unit="s", utc=True),
                end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=daily.Interval()),
                inclusive="left"
            ),
            "weather_code": daily.Variables(0).ValuesAsNumpy(),
            "temperature_max": daily.Variables(1).ValuesAsNumpy(),
            "temperature_min": daily.Variables(2).ValuesAsNumpy(),
            "precipitation_sum": daily.Variables(3).ValuesAsNumpy(),
            "precipitation_hours": daily.Variables(4).ValuesAsNumpy(),
            "precipitation_probability": daily.Variables(5).ValuesAsNumpy(),
            "wind_speed_max": daily.Variables(6).ValuesAsNumpy()
        }).to_dict('records')

        return {
            'current': current_data,
            'forecast': forecast_data
        }

    except Exception as e:
        print(f"Error fetching weather data: {str(e)}")
        return None

def get_weather_description(weather_code):
    """Convert weather code to description."""
    weather_codes = {
        0: 'Clear sky',
        1: 'Mainly clear',
        2: 'Partly cloudy',
        3: 'Overcast',
        45: 'Fog',
        48: 'Depositing rime fog',
        51: 'Drizzle: Light intensity',
        53: 'Drizzle: Moderate intensity',
        55: 'Drizzle: Dense intensity',
        56: 'Freezing Drizzle: Light intensity',
        57: 'Freezing Drizzle: Dense intensity',
        61: 'Rain: Slight intensity',
        63: 'Rain: Moderate intensity',
        65: 'Rain: Heavy intensity',
        66: 'Freezing Rain: Light intensity',
        67: 'Freezing Rain: Heavy intensity',
        71: 'Snow fall: Slight intensity',
        73: 'Snow fall: Moderate intensity',
        75: 'Snow fall: Heavy intensity',
        77: 'Snow grains',
        80: 'Rain showers: Slight',
        81: 'Rain showers: Moderate',
        82: 'Rain showers: Violent',
        85: 'Snow showers slight',
        86: 'Snow showers Heavy',
        95: 'Thunderstorm: Slight or moderate',
        96: 'Thunderstorm with slight hail',
        99: 'Thunderstorm with heavy hail'
    }
    return weather_codes.get(int(weather_code), 'Unknown')

def generate_color_palette(city, weather_description, current_temperature,
                         current_precipitation, current_cloud_cover,
                         current_wind_speed, is_day):
    """Generate color palette using Anthropic API."""
    try:
        client = Anthropic()
        
        prompt = f"""
        You are a leading visual designer. You goal is to design attractive, vibrant color palettes for a weather app. 
        Each color palette is inspired by the unique atmosphere of a city and current weather conditions.
        The city and current weather conditions are:
        - location: {city}
        - weather description: {weather_description}
        - current temperature: {current_temperature}
        - current precipitation: {current_precipitation}
        - current cloud cover: {current_cloud_cover}
        - current wind speed: {current_wind_speed}
        - is it day or night: {is_day} (0 is night, 1 is day)
        
        Based on those inputs, using the 60-30-10 rule suggest a well-balanced color palette that works well for websites and consists of three colors: 
        - dominant color
        - secondary color 
        - accent color
        
        Be very creative when designing the palette and remember to have it inspired by the unique atmosphere of the city and the current weather conditions.
        """

        messages = [{
            "role": "user",
            "content": f"""{prompt}.
            Requirements:
            - The output must be valid JSON
            - Use ONLY the following keys: dominant_color, secondary_color, accent_color
            - Each key should get a hex color code
            - Do not include any explanation or other text
            - Each value should be of type string (str)
            """
        }]

        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            temperature=0.7,
            messages=messages
        )

        colors = json.loads(response.content[0].text)
        return colors

    except Exception as e:
        print(f"Error generating color palette: {str(e)}")
        return None

import os
import time
import json
import requests
from datetime import datetime, timedelta
from flask import url_for
from werkzeug.utils import secure_filename

def generate_city_image(city, weather_description):
    """Generate or retrieve a cached city image based on city and weather description."""
    try:
        # Define directories for static images and cache
        static_dir = os.path.join('sentient-weather', 'static', 'images')
        cache_dir = os.path.join('sentient-weather', 'static', 'images')
        os.makedirs(static_dir, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)

        # Create a cache key based on the city and weather description
        safe_city_name = secure_filename(city.lower())
        safe_weather = secure_filename(weather_description.lower())
        cache_key = f"{safe_city_name}_{safe_weather}"
        
        # Paths for cached data
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")

        # Check if the cache is valid (exists and is less than 24 hours old)
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
                
            cache_timestamp = datetime.fromtimestamp(cache_data['timestamp'])
            if datetime.now() - cache_timestamp < timedelta(hours=24):
                # Cache is valid
                image_path = cache_data['image_path']
                if os.path.exists(os.path.join('static', image_path.lstrip('/static/'))):
                    print(f"Using cached image for {city} with {weather_description}")
                    return image_path

        # If cache is invalid or doesn't exist, generate a new image
        from openai import OpenAI  # Make sure OpenAI API client is available and configured
        client = OpenAI()

        # Generate a unique filename for the image
        timestamp = int(time.time())
        filename = f"{cache_key}_{timestamp}.png"
        image_path = os.path.join(static_dir, filename)

        # Clean up older images (keeping only the 20 most recent images)
        existing_images = sorted(
            ((os.path.join(static_dir, f), os.path.getctime(os.path.join(static_dir, f)))
             for f in os.listdir(static_dir) if f.endswith('.png')),
            key=lambda x: x[1],
            reverse=True
        )
        if len(existing_images) > 20:
            for old_image, _ in existing_images[20:]:
                try:
                    os.remove(old_image)
                    # Remove corresponding cache file if it exists
                    old_cache = os.path.join(cache_dir, f"{os.path.splitext(os.path.basename(old_image))[0]}.json")
                    if os.path.exists(old_cache):
                        os.remove(old_cache)
                except Exception as e:
                    print(f"Error removing old file {old_image}: {str(e)}")

        # Generate the image using OpenAI's DALL-E API
        prompt = f"An oil painting of the most iconic scenery from {city} where the weather is {weather_description}."
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )

        # Check the response and download the image
        image_url = response.data[0].url
        img_response = requests.get(image_url)
        if img_response.status_code == 200:
            with open(image_path, 'wb') as f:
                f.write(img_response.content)

            # Create a cache entry for the newly generated image
            relative_path = url_for('static', filename=f'images/{filename}')
            cache_data = {
                'timestamp': timestamp,
                'image_path': relative_path,
                'city': city,
                'weather': weather_description
            }
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f)

            print(f"Generated new image for {city} with {weather_description}")
            return relative_path
        else:
            print(f"Failed to download image: Status code {img_response.status_code}")
            return None

    except Exception as e:
        print(f"Error generating city image: {str(e)}")
        return None
